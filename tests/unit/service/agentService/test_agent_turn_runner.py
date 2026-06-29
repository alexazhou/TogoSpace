"""AgentTurnRunner 单元测试：测试 Turn 执行逻辑。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStatus, AgentHistoryTag, AgentTaskType, DriverType, OpenaiApiRole, RoomState, TurnStepResult
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtScheculeTask import GtScheculeTask
from model.dbModel.gtRoomMessage import GtRoomMessage
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.toolRegistry import ToolExecutionResult
from service.agentService.driver.base import AgentDriverConfig
from service.roomService import ChatRoom
import service.roomService as roomService
from util import llmApiUtil


def _make_turn_runner() -> AgentTurnRunner:
    """构造一个最小可运行的 TurnRunner，driver 使用默认 NATIVE。"""
    gt_agent = GtAgent(id=1, team_id=1, name="TestAgent", role_template_id=1, model="mock")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )
    # 替换 _history 为 mock，避免单元测试触及数据库
    mock_history = MagicMock()
    mock_history.has_active_turn = MagicMock(return_value=False)
    mock_history.append_history_message = AsyncMock()
    mock_history.finalize_history_item = AsyncMock()
    mock_history.export_openai_tools = MagicMock(return_value=[])
    runner._history = mock_history
    return runner


@pytest.fixture
def turn_runner():
    return _make_turn_runner()


@pytest.fixture(autouse=True)
def mock_get_control_room(monkeypatch):
    """单元测试不初始化 DB，mock 掉 get_control_room_for_agent 避免 peewee 报错。"""
    monkeypatch.setattr(roomService, "get_control_room_for_agent", AsyncMock(return_value=None))


from util.assertUtil import MakeSureException


@pytest.mark.asyncio
async def test_run_task_turn_raises_when_room_id_missing(turn_runner):
    task = MagicMock(spec=GtScheculeTask)
    task.id = 100
    task.task_data = {}  # 无 room_id

    with pytest.raises(MakeSureException, match="缺少 room_id"):
        await turn_runner.run_task_turn(task)


@pytest.mark.asyncio
async def test_run_task_turn_raises_when_room_not_found(turn_runner):
    task = MagicMock(spec=GtScheculeTask)
    task.id = 100
    task.task_data = {"room_id": 999}

    with patch("service.agentService.agentTurnRunner.roomService") as mock_room_service:
        mock_room_service.get_room = MagicMock(return_value=None)

        with pytest.raises(MakeSureException, match="不存在"):
            await turn_runner.run_task_turn(task)


@pytest.mark.asyncio
async def test_pull_room_messages_syncs_to_history(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.team_id = 1

    msg = MagicMock(spec=GtRoomMessage)
    msg.sender_id = 2  # 非 agent 自身
    msg.sender_display_name = "OtherAgent"
    msg.content = "Hello"

    room.get_unread_messages = AsyncMock(return_value=[msg])

    with patch("service.agentService.agentTurnRunner.agentActivityService.add_activity", new=AsyncMock()):
        count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 1
    turn_runner._history.append_history_message.assert_called_once()
    call_args = turn_runner._history.append_history_message.call_args
    item = call_args.args[0]  # 第一个位置参数是 GtAgentHistory
    assert item.role == OpenaiApiRole.USER
    assert AgentHistoryTag.ROOM_TURN_BEGIN in item.tags


@pytest.mark.asyncio
async def test_pull_room_messages_skips_own_messages(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"

    # 自己发的消息，应跳过
    msg = MagicMock(spec=GtRoomMessage)
    msg.sender_id = 1  # agent.gt_agent.id
    msg.sender_display_name = "TestAgent"
    msg.content = "My message"

    room.get_unread_messages = AsyncMock(return_value=[msg])

    count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 0
    turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_pull_room_messages_returns_zero_when_empty(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.get_unread_messages = AsyncMock(return_value=[])

    count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 0
    turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_turn_loop_does_not_stop_on_long_tool_chain(turn_runner):
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(max_retries=3, hint_prompt="hint")
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TURN_DONE,
    ])

    await turn_runner._run_turn_loop(room)

    assert turn_runner._advance_step.await_count == 7


@pytest.mark.asyncio
async def test_run_turn_loop_retries_failed_action_by_max_retries(turn_runner):
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(max_retries=2, hint_prompt="retry hint")
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.LLM_OUTPUT_NO_ACTION,
        TurnStepResult.LLM_OUTPUT_NO_ACTION,
        TurnStepResult.LLM_OUTPUT_NO_ACTION,
    ])
    turn_runner._history.get_last_assistant_message = MagicMock(return_value=llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.ASSISTANT, "plain text"))

    with pytest.raises(RuntimeError, match="达到失败行动重试上限仍未完成行动"):
        await turn_runner._run_turn_loop(room)

    assert turn_runner._history.append_history_message.await_count == 2


@pytest.mark.asyncio
async def test_advance_step_continues_to_infer_when_tool_failed(turn_runner):
    """单 tool 失败且无剩余 pending tool 时，允许进入下一次 assistant 推理。"""
    room = MagicMock(spec=ChatRoom)
    failed_tool_item = GtAgentHistory.build(
        llmApiUtil.OpenAIMessage.tool_result("tool-call-1", '{"success":false,"message":"boom"}'),
        status=AgentHistoryStatus.FAILED,
        error_message="boom",
    )
    assistant_output_item = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT)
    assistant_message = llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.ASSISTANT, "handled tool failure")
    turn_runner._history.last = MagicMock(return_value=failed_tool_item)
    turn_runner._history.append_history_init_item = AsyncMock(return_value=assistant_output_item)
    turn_runner._infer_to_item = AsyncMock(return_value=assistant_message)
    turn_runner._history.find_tool_call_by_id = MagicMock()
    turn_runner._history.get_first_pending_tool_call = MagicMock(return_value=None)

    result = await turn_runner._advance_step(room, [])

    assert result == TurnStepResult.LLM_OUTPUT_NO_ACTION
    # 没有剩余 tool_call 时，FAILED tool 会被当作普通上下文继续交给模型处理。
    turn_runner._history.append_history_init_item.assert_awaited_once_with(role=OpenaiApiRole.ASSISTANT)
    turn_runner._infer_to_item.assert_awaited_once_with(assistant_output_item, [], tool_choice=None)
    turn_runner._history.find_tool_call_by_id.assert_not_called()
    turn_runner._history.get_first_pending_tool_call.assert_called_once_with()


@pytest.mark.asyncio
async def test_advance_step_continues_to_pending_tool_when_previous_tool_failed(turn_runner):
    """多 tool 场景下，前一个 tool 失败后也必须先补跑剩余 tool_call，不能直接继续推理。"""
    room = MagicMock(spec=ChatRoom)
    failed_tool_item = GtAgentHistory.build(
        llmApiUtil.OpenAIMessage.tool_result("tool-call-1", '{"success":false,"message":"boom"}'),
        status=AgentHistoryStatus.FAILED,
        error_message="boom",
    )
    pending_tool_call = llmApiUtil.OpenAIToolCall(
        id="tool-call-2",
        function={"name": "read_file", "arguments": '{"file_path": "/tmp/foo.txt"}'},
    )
    turn_runner._history.last = MagicMock(return_value=failed_tool_item)
    turn_runner._history.append_history_init_item = AsyncMock()
    turn_runner._infer_to_item = AsyncMock()
    turn_runner._history.find_tool_call_by_id = MagicMock()
    turn_runner._history.get_first_pending_tool_call = MagicMock(return_value=pending_tool_call)

    result = await turn_runner._advance_step(room, [])

    assert result == TurnStepResult.TOOL_EXECUTE_SUCCESS
    # 这里的关键断言是：要为剩余 tool_call 追加 TOOL placeholder，
    # 后续由下一轮 _advance_step 真正执行它，保证 tool_use / tool_result 链闭合。
    turn_runner._history.append_history_init_item.assert_awaited_once_with(
        role=OpenaiApiRole.TOOL,
        tool_call_id="tool-call-2",
    )
    turn_runner._infer_to_item.assert_not_awaited()
    turn_runner._history.find_tool_call_by_id.assert_not_called()
    turn_runner._history.get_first_pending_tool_call.assert_called_once_with()


@pytest.mark.asyncio
async def test_infer_and_classify_returns_error_action_on_json_content(turn_runner):
    """content 为 JSON 对象、无 tool_calls 时返回 LLM_OUTPUT_ERROR，不写任何 tool 记录。"""
    output_item = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT)
    assistant_message = MagicMock()
    assistant_message.content = '{"room_name": "test", "msg": "hello"}'
    assistant_message.tool_calls = None
    turn_runner._infer_to_item = AsyncMock(return_value=assistant_message)

    result = await turn_runner._infer_and_classify(output_item, [])

    assert result == TurnStepResult.LLM_OUTPUT_ERROR
    turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_infer_and_classify_writes_failed_tool_records_on_json_content_with_tool_calls(turn_runner):
    """content 为 JSON 对象 + 有 tool_calls 时，为每个 tool_call 写 FAILED 记录，并返回 LLM_OUTPUT_ERROR。"""
    output_item = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT)
    tc = MagicMock()
    tc.id = "tc-123"
    assistant_message = MagicMock()
    assistant_message.content = '{"room_name": "test", "msg": "hello"}'
    assistant_message.tool_calls = [tc]
    turn_runner._infer_to_item = AsyncMock(return_value=assistant_message)

    result = await turn_runner._infer_and_classify(output_item, [])

    assert result == TurnStepResult.LLM_OUTPUT_ERROR
    turn_runner._history.append_history_message.assert_awaited_once()
    written_item = turn_runner._history.append_history_message.call_args.args[0]
    assert written_item.role == OpenaiApiRole.TOOL
    assert written_item.status == AgentHistoryStatus.FAILED
    assert written_item.tool_call_id == "tc-123"


@pytest.mark.asyncio
async def test_run_turn_loop_retries_on_error_action_with_error_hint(turn_runner):
    """LLM_OUTPUT_ERROR 时注入 hint_prompt_error_action 并重试，最终 TURN_DONE。"""
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="generic hint", hint_prompt_error_action="error hint"
    )
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.LLM_OUTPUT_ERROR,
        TurnStepResult.LLM_OUTPUT_ERROR,
        TurnStepResult.TURN_DONE,
    ])

    await turn_runner._run_turn_loop(room)

    assert turn_runner._history.append_history_message.await_count == 2


@pytest.mark.asyncio
async def test_run_tool_to_item_persists_tool_result_into_activity_metadata(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.team_id = 1
    room.state = RoomState.IDLE
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 99
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tool-call-1",
        function={"name": "read_file", "arguments": '{"file_path": "/tmp/demo.txt"}'},
    )

    turn_runner.tool_registry.execute_tool_call = AsyncMock(return_value=ToolExecutionResult(
        tool_call_id="tool-call-1",
        result={"success": True, "content": "demo"},
        success=True,
    ))
    turn_runner.tool_registry.get_registered_tool = MagicMock(return_value=SimpleNamespace(marks_turn_finish=False, self_interrupt=False))

    with patch("service.agentService.agentTurnRunner.agentActivityService.add_activity", new=AsyncMock(return_value=MagicMock(id=7))) as mock_add_activity, patch(
        "service.agentService.agentTurnRunner.agentActivityService.update_activity_progress",
        new=AsyncMock(),
    ) as mock_update_activity:
        result = await turn_runner._run_tool_to_item(tool_call, output_item, room)

    assert result == TurnStepResult.TOOL_EXECUTE_SUCCESS
    mock_add_activity.assert_awaited_once()
    turn_runner._history.finalize_history_item.assert_awaited_once()
    mock_update_activity.assert_awaited_once()
    metadata_patch = mock_update_activity.await_args.kwargs["metadata_patch"]
    assert metadata_patch.tool_result == {"success": True, "content": "demo"}


@pytest.mark.asyncio
async def test_run_turn_loop_raises_on_error_action_after_max_retries(turn_runner):
    """LLM_OUTPUT_ERROR 超出 max_retries 后抛出 RuntimeError。"""
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="generic hint", hint_prompt_error_action="error hint"
    )
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.LLM_OUTPUT_ERROR,
        TurnStepResult.LLM_OUTPUT_ERROR,
        TurnStepResult.LLM_OUTPUT_ERROR,
    ])

    with pytest.raises(RuntimeError, match="达到 ERROR_ACTION 重试上限"):
        await turn_runner._run_turn_loop(room)

    assert turn_runner._history.append_history_message.await_count == 2


from util import configUtil
from util.configTypes import AppConfig, SettingConfig


@pytest.mark.asyncio
async def test_resolve_model_returns_configured_model(monkeypatch):
    """resolve_model 应返回配置的模型。"""
    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc", "lightweight": "", "vision": ""},
            llm_providers=[
                {
                    "name": "svc",
                    "enable": True,
                    "urls": {"openai": "http://localhost/v1/chat/completions"},
                    "api_key": "key-123",
                    "type": "openai",
                    "models": [
                        {
                            "name": "configured-model",
                            "protocol": "openai",
                            "context_config": {
                                "context_window_tokens": 128000,
                                "reserve_output_tokens": 8192,
                                "compact_trigger_ratio": 0.85
                            }
                        }
                    ]
                }
            ],
    )))

    from service.llmService.core import resolve_model
    provider_config, llm_config = resolve_model("configured-model@svc")

    assert provider_config.name == "svc"
    assert llm_config.name == "configured-model"


@pytest.mark.asyncio
async def test_resolve_model_uses_default_when_empty(monkeypatch):
    """model 为空时，resolve_model 使用 default_models.primary。"""
    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc", "lightweight": "", "vision": ""},
            llm_providers=[
                {
                    "name": "svc",
                    "enable": True,
                    "urls": {"openai": "http://localhost/v1/chat/completions"},
                    "api_key": "key-123",
                    "type": "openai",
                    "models": [
                        {
                            "name": "configured-model",
                            "protocol": "openai",
                            "context_config": {
                                "context_window_tokens": 128000,
                                "reserve_output_tokens": 8192,
                                "compact_trigger_ratio": 0.85
                            }
                        }
                    ]
                }
            ],
    )))

    from service.llmService.core import resolve_model
    provider_config, llm_config = resolve_model(None)

    assert provider_config.name == "svc"
    assert llm_config.name == "configured-model"


# ─── handle_cancel_turn 相关测试 ─────────────────────────────


@pytest.mark.asyncio
async def test_handle_cancel_turn_calls_driver_then_history(turn_runner):
    """handle_cancel_turn 应依次调用 driver.cancel_turn、history.finalize_cancel_turn，再批量失败化 STARTED activity。"""
    turn_runner.driver = MagicMock()
    turn_runner.driver.cancel_turn = AsyncMock()
    turn_runner._history.finalize_cancel_turn = AsyncMock()
    turn_runner._current_room = MagicMock(spec=ChatRoom)

    call_order = []
    turn_runner.driver.cancel_turn.side_effect = lambda: call_order.append("driver")
    turn_runner._current_room.cancel_current_turn.side_effect = lambda: call_order.append("room")
    turn_runner._history.finalize_cancel_turn.side_effect = lambda: call_order.append("history")

    with patch("service.agentService.agentTurnRunner.agentActivityService.fail_started_activities", new=AsyncMock(side_effect=lambda *args, **kwargs: call_order.append("activity"))) as mock_fail:
        await turn_runner.handle_cancel_turn()

    turn_runner.driver.cancel_turn.assert_awaited_once()
    turn_runner._current_room.cancel_current_turn.assert_called_once_with()
    turn_runner._history.finalize_cancel_turn.assert_awaited_once()
    mock_fail.assert_awaited_once_with(turn_runner.gt_agent.id, error_message="cancelled by user")
    assert call_order == ["driver", "room", "history", "activity"]


# ─── 任务驱动型唤醒（TODO_TASK）相关测试 ──────────────────────────────


@pytest.mark.asyncio
async def test_run_task_turn_room_message_still_checks_room_id(turn_runner):
    """ROOM_MESSAGE 类型仍走原有路径，缺少 room_id 时报错。"""
    from constants import AgentTaskType
    task = MagicMock(spec=GtScheculeTask)
    task.id = 100
    task.task_type = AgentTaskType.ROOM_MESSAGE
    task.task_data = {}

    from util.assertUtil import MakeSureException
    with pytest.raises(MakeSureException, match="缺少 room_id"):
        await turn_runner.run_task_turn(task)



@pytest.mark.asyncio
async def test_run_task_turn_todo_task_raises_when_agent_task_id_missing(turn_runner):
    """TODO_TASK 模式缺少 agent_task_id 时应报错。"""
    from constants import AgentTaskType
    task = MagicMock(spec=GtScheculeTask)
    task.id = 200
    task.task_type = AgentTaskType.TODO_TASK
    task.task_data = {}  # 无 agent_task_id

    from util.assertUtil import MakeSureException
    with pytest.raises(MakeSureException, match="缺少 agent_task_id"):
        await turn_runner.run_task_turn(task)


@pytest.mark.asyncio
async def test_run_task_turn_todo_task_raises_when_agent_task_not_found(turn_runner):
    """TODO_TASK 模式 agent_task_id 对应记录不存在时应报错。"""
    from constants import AgentTaskType
    task = MagicMock(spec=GtScheculeTask)
    task.id = 200
    task.task_type = AgentTaskType.TODO_TASK
    task.task_data = {"agent_task_id": 999}

    from util.assertUtil import MakeSureException
    with patch("dal.db.gtAgentTaskManager.get_task", new=AsyncMock(return_value=None)):
        with pytest.raises(MakeSureException, match="不存在"):
            await turn_runner.run_task_turn(task)



@pytest.mark.asyncio
async def test_run_turn_loop_skips_room_checks_when_no_room(turn_runner):
    """room=None 时，_run_turn_loop 不应尝试检查即时消息（不 crash）。"""
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(max_retries=1, hint_prompt="hint")
    turn_runner._advance_step = AsyncMock(return_value=TurnStepResult.TURN_DONE)

    # 不传 room（None）→ 不应抛出任何异常
    await turn_runner._run_turn_loop(room=None)

    turn_runner._advance_step.assert_awaited_once()


# ─── finish 类工具失败防死循环测试 ──────────────────────────────


def _make_finish_tool_exec_result(success: bool) -> ToolExecutionResult:
    if success:
        return ToolExecutionResult(tool_call_id="tc-finish", result={"success": True}, success=True)
    return ToolExecutionResult(
        tool_call_id="tc-finish",
        result={"success": False, "message": "未在收到消息的房间发言"},
        success=False,
        error_message="未在收到消息的房间发言",
    )


@pytest.mark.asyncio
async def test_run_tool_to_item_returns_turn_done_when_finish_tool_succeeds(turn_runner):
    """marks_turn_finish=True 且执行成功时，应返回 TURN_DONE。"""
    room = MagicMock(spec=ChatRoom)
    room.team_id = 1
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 10
    output_item.tags = []
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc-finish", function={"name": "finish_action", "arguments": "{}"}
    )

    turn_runner.tool_registry.execute_tool_call = AsyncMock(
        return_value=_make_finish_tool_exec_result(success=True)
    )
    turn_runner.tool_registry.get_registered_tool = MagicMock(
        return_value=SimpleNamespace(marks_turn_finish=True, self_interrupt=False)
    )

    with patch("service.agentService.agentTurnRunner.agentActivityService.add_activity", new=AsyncMock(return_value=MagicMock(id=1))), \
         patch("service.agentService.agentTurnRunner.agentActivityService.update_activity_progress", new=AsyncMock()):
        result = await turn_runner._run_tool_to_item(tool_call, output_item, room)

    assert result == TurnStepResult.TURN_DONE


@pytest.mark.asyncio
async def test_run_tool_to_item_returns_error_action_when_finish_tool_fails(turn_runner):
    """marks_turn_finish=True 且执行失败时，应返回 TOOL_EXECUTE_FAILED_FINISH（触发 failed_action_count）。"""
    room = MagicMock(spec=ChatRoom)
    room.team_id = 1
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 11
    output_item.tags = []
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc-finish", function={"name": "finish_action", "arguments": "{}"}
    )

    turn_runner.tool_registry.execute_tool_call = AsyncMock(
        return_value=_make_finish_tool_exec_result(success=False)
    )
    turn_runner.tool_registry.get_registered_tool = MagicMock(
        return_value=SimpleNamespace(marks_turn_finish=True, self_interrupt=False)
    )

    with patch("service.agentService.agentTurnRunner.agentActivityService.add_activity", new=AsyncMock(return_value=MagicMock(id=2))), \
         patch("service.agentService.agentTurnRunner.agentActivityService.update_activity_progress", new=AsyncMock()):
        result = await turn_runner._run_tool_to_item(tool_call, output_item, room)

    assert result == TurnStepResult.TOOL_EXECUTE_FAILED_FINISH


@pytest.mark.asyncio
async def test_run_turn_loop_raises_after_repeated_finish_failures(turn_runner):
    """finish 类工具反复失败时，_run_turn_loop 应在超出 max_retries 后抛出 RuntimeError（防死循环）。
    TOOL_EXECUTE_FAILED_FINISH 不注入 hint prompt（tool_result 已包含具体错误信息）。"""
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="hint", hint_prompt_error_action="请正确调用 finish_action"
    )
    # 模拟 LLM 一直调用 finish_action 但一直失败
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # finish 失败 1
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # finish 失败 2
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # finish 失败 3 → 超限
    ])

    with pytest.raises(RuntimeError, match="达到 finish 失败重试上限"):
        await turn_runner._run_turn_loop(room)

    # finish 失败不注入 hint prompt，只通过 tool_result 提供反馈
    assert turn_runner._history.append_history_message.await_count == 0


@pytest.mark.asyncio
async def test_run_turn_loop_does_not_count_normal_tool_failures(turn_runner):
    """普通工具（marks_turn_finish=False）失败后返回 TOOL_EXECUTE_SUCCESS，不应计入 failed_action_count。"""
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=1, hint_prompt="hint", hint_prompt_error_action="error hint"
    )
    # 普通工具失败返回 TOOL_EXECUTE_SUCCESS，不会触发计数；最终调用 finish 成功
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.TOOL_EXECUTE_SUCCESS,   # 普通工具失败，但仍是 TOOL_EXECUTE_SUCCESS
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TOOL_EXECUTE_SUCCESS,
        TurnStepResult.TURN_DONE,  # finish 成功
    ])

    await turn_runner._run_turn_loop(room)  # 不应抛异常

    assert turn_runner._advance_step.await_count == 4
    turn_runner._history.append_history_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_turn_loop_finish_failure_reset_by_tool_success(turn_runner):
    """finish 失败后被 TOOL_EXECUTE_SUCCESS（如 LLM 调用了 send_chat_msg）清零计数器，不会误触发 RuntimeError。
    模拟真实场景：LLM 在 finish_action 失败后做了一次正常发言，然后再次 finish_action 失败。
    TOOL_EXECUTE_FAILED_FINISH 不注入 hint prompt（tool_result 已含具体错误）。
    """
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="hint", hint_prompt_error_action="请正确调用 finish_action"
    )
    # finish失败 → 发消息成功(清零) → finish失败 → finish失败 → finish失败(超限)
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # finish 失败 1 (count=1)
        TurnStepResult.TOOL_EXECUTE_SUCCESS,          # 正常工具成功，清零 (count=0)
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # finish 失败 (count=1)
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # finish 失败 (count=2)
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # finish 失败 (count=3 → 超限 max=2)
    ])

    with pytest.raises(RuntimeError, match="达到 finish 失败重试上限"):
        await turn_runner._run_turn_loop(room)

    # finish 失败不注入 hint prompt，计数器重置仍然正常工作
    assert turn_runner._history.append_history_message.await_count == 0


@pytest.mark.asyncio
async def test_run_turn_loop_llm_output_tool_calls_does_not_reset_counter(turn_runner):
    """LLM_OUTPUT_TOOL_CALLS 不应重置 failed_action_count。
    模拟：finish失败 → LLM生成tool_calls(不重置) → finish失败 → 继续重试。
    TOOL_EXECUTE_FAILED_FINISH 不注入 hint prompt（tool_result 已含具体错误）。
    """
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="hint", hint_prompt_error_action="请正确调用 finish_action"
    )
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # count=1
        TurnStepResult.LLM_OUTPUT_TOOL_CALLS,         # 不变，count 仍为 1
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # count=2
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,   # count=3 → 超限
    ])

    with pytest.raises(RuntimeError, match="达到 finish 失败重试上限"):
        await turn_runner._run_turn_loop(room)

    # finish 失败不注入 hint prompt
    assert turn_runner._history.append_history_message.await_count == 0


@pytest.mark.asyncio
async def test_run_turn_loop_consecutive_finish_failures_with_max_retries_5(turn_runner):
    """max_retries=5 时，连续 finish 失败 6 次后抛出 RuntimeError。
    TOOL_EXECUTE_FAILED_FINISH 不注入 hint prompt（tool_result 已含具体错误）。
    """
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=5, hint_prompt="hint", hint_prompt_error_action="请重试 finish_action"
    )
    # 连续 6 次 finish 失败（第 6 次超出 max_retries=5）
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # count=1
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # count=2
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # count=3
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # count=4
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # count=5
        TurnStepResult.TOOL_EXECUTE_FAILED_FINISH,  # count=6 → 超限
    ])

    with pytest.raises(RuntimeError, match="达到 finish 失败重试上限"):
        await turn_runner._run_turn_loop(room)

    # finish 失败不注入 hint prompt
    assert turn_runner._history.append_history_message.await_count == 0


