"""AgentTurnRunner 单元测试：测试 Turn 执行逻辑。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStatus, AgentHistoryTag, DriverType, OpenaiApiRole, TurnStepResult
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from service.roomService import ChatRoom
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
    mock_history.export_openai_tools = MagicMock(return_value=[])
    runner._history = mock_history
    return runner


@pytest.fixture
def turn_runner():
    return _make_turn_runner()


from util.assertUtil import MakeSureException


@pytest.mark.asyncio
async def test_run_chat_turn_raises_when_room_id_missing(turn_runner):
    task = MagicMock(spec=GtAgentTask)
    task.id = 100
    task.task_data = {}  # 无 room_id

    with pytest.raises(MakeSureException, match="缺少 room_id"):
        await turn_runner.run_chat_turn(task)


@pytest.mark.asyncio
async def test_run_chat_turn_raises_when_room_not_found(turn_runner):
    task = MagicMock(spec=GtAgentTask)
    task.id = 100
    task.task_data = {"room_id": 999}

    with patch("service.agentService.agentTurnRunner.roomService") as mock_room_service:
        mock_room_service.get_room = MagicMock(return_value=None)

        with pytest.raises(MakeSureException, match="不存在"):
            await turn_runner.run_chat_turn(task)


@pytest.mark.asyncio
async def test_pull_room_messages_syncs_to_history(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.team_id = 1

    msg = MagicMock(spec=GtCoreRoomMessage)
    msg.sender_id = 2  # 非 agent 自身
    msg.sender_name = "OtherAgent"
    msg.content = "Hello"

    room.get_unread_messages = AsyncMock(return_value=[msg])

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
    msg = MagicMock(spec=GtCoreRoomMessage)
    msg.sender_id = 1  # agent.gt_agent.id
    msg.sender_name = "TestAgent"
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
        TurnStepResult.CONTINUE,
        TurnStepResult.CONTINUE,
        TurnStepResult.CONTINUE,
        TurnStepResult.CONTINUE,
        TurnStepResult.CONTINUE,
        TurnStepResult.CONTINUE,
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
        TurnStepResult.NO_ACTION,
        TurnStepResult.NO_ACTION,
        TurnStepResult.NO_ACTION,
    ])
    turn_runner._history.get_last_assistant_message = MagicMock(return_value=llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.ASSISTANT, "plain text"))

    with pytest.raises(RuntimeError, match="达到失败行动重试上限仍未完成行动"):
        await turn_runner._run_turn_loop(room)

    assert turn_runner._history.append_history_message.await_count == 2


@pytest.mark.asyncio
async def test_advance_step_continues_to_infer_when_tool_failed(turn_runner):
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
    turn_runner._history.get_first_pending_tool_call = MagicMock()

    result = await turn_runner._advance_step(room, [])

    assert result == TurnStepResult.NO_ACTION
    turn_runner._history.append_history_init_item.assert_awaited_once_with(role=OpenaiApiRole.ASSISTANT)
    turn_runner._infer_to_item.assert_awaited_once_with(assistant_output_item, [], tool_choice=None)
    turn_runner._history.find_tool_call_by_id.assert_not_called()
    turn_runner._history.get_first_pending_tool_call.assert_not_called()


@pytest.mark.asyncio
async def test_infer_and_classify_returns_error_action_on_json_content(turn_runner):
    """content 为 JSON 对象、无 tool_calls 时返回 ERROR_ACTION，不写任何 tool 记录。"""
    output_item = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT)
    assistant_message = MagicMock()
    assistant_message.content = '{"room_name": "test", "msg": "hello"}'
    assistant_message.tool_calls = None
    turn_runner._infer_to_item = AsyncMock(return_value=assistant_message)

    result = await turn_runner._infer_and_classify(output_item, [])

    assert result == TurnStepResult.ERROR_ACTION
    turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_infer_and_classify_writes_failed_tool_records_on_json_content_with_tool_calls(turn_runner):
    """content 为 JSON 对象 + 有 tool_calls 时，为每个 tool_call 写 FAILED 记录，并返回 ERROR_ACTION。"""
    output_item = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT)
    tc = MagicMock()
    tc.id = "tc-123"
    assistant_message = MagicMock()
    assistant_message.content = '{"room_name": "test", "msg": "hello"}'
    assistant_message.tool_calls = [tc]
    turn_runner._infer_to_item = AsyncMock(return_value=assistant_message)

    result = await turn_runner._infer_and_classify(output_item, [])

    assert result == TurnStepResult.ERROR_ACTION
    turn_runner._history.append_history_message.assert_awaited_once()
    written_item = turn_runner._history.append_history_message.call_args.args[0]
    assert written_item.role == OpenaiApiRole.TOOL
    assert written_item.status == AgentHistoryStatus.FAILED
    assert written_item.tool_call_id == "tc-123"


@pytest.mark.asyncio
async def test_run_turn_loop_retries_on_error_action_with_error_hint(turn_runner):
    """ERROR_ACTION 时注入 hint_prompt_error_action 并重试，最终 TURN_DONE。"""
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="generic hint", hint_prompt_error_action="error hint"
    )
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.ERROR_ACTION,
        TurnStepResult.ERROR_ACTION,
        TurnStepResult.TURN_DONE,
    ])

    await turn_runner._run_turn_loop(room)

    assert turn_runner._history.append_history_message.await_count == 2


@pytest.mark.asyncio
async def test_run_turn_loop_raises_on_error_action_after_max_retries(turn_runner):
    """ERROR_ACTION 超出 max_retries 后抛出 RuntimeError。"""
    room = MagicMock(spec=ChatRoom)
    turn_runner.driver = MagicMock()
    turn_runner.driver.turn_setup = SimpleNamespace(
        max_retries=2, hint_prompt="generic hint", hint_prompt_error_action="error hint"
    )
    turn_runner._advance_step = AsyncMock(side_effect=[
        TurnStepResult.ERROR_ACTION,
        TurnStepResult.ERROR_ACTION,
        TurnStepResult.ERROR_ACTION,
    ])

    with pytest.raises(RuntimeError, match="达到 ERROR_ACTION 重试上限"):
        await turn_runner._run_turn_loop(room)

    assert turn_runner._history.append_history_message.await_count == 2


from util import configUtil
from util.configTypes import AppConfig, SettingConfig


@pytest.mark.asyncio
async def test_resolve_compact_config_uses_agent_model_when_set(monkeypatch):
    """Agent model 有值时，_resolve_compact_config 返回 Agent 的 model。"""
    gt_agent = GtAgent(id=1, team_id=1, name="TestAgent", role_template_id=1, model="agent-model")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_llm_server="svc",
        llm_services=[
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1/chat/completions",
                "api_key": "key-123",
                "type": "openai-compatible",
                "model": "configured-model",
                "context_window_tokens": 128000,
                "reserve_output_tokens": 8192,
                "compact_trigger_ratio": 0.85,
            }
        ],
    )))

    resolved_model, llm_config, trigger_tokens, hard_limit_tokens = runner._resolve_compact_config()

    assert resolved_model == "agent-model"


@pytest.mark.asyncio
async def test_resolve_compact_config_uses_config_model_when_agent_model_empty(monkeypatch):
    """Agent model 为空时，_resolve_compact_config 返回配置中的 model。"""
    gt_agent = GtAgent(id=1, team_id=1, name="TestAgent", role_template_id=1, model="")  # model 为空
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_llm_server="svc",
        llm_services=[
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1/chat/completions",
                "api_key": "key-123",
                "type": "openai-compatible",
                "model": "configured-model",
                "context_window_tokens": 128000,
                "reserve_output_tokens": 8192,
                "compact_trigger_ratio": 0.85,
            }
        ],
    )))

    resolved_model, llm_config, trigger_tokens, hard_limit_tokens = runner._resolve_compact_config()

    assert resolved_model == "configured-model"


# ─── handle_cancel_turn 相关测试 ─────────────────────────────


@pytest.mark.asyncio
async def test_handle_cancel_turn_calls_driver_then_history(turn_runner):
    """handle_cancel_turn 应依次调用 driver.cancel_turn 和 history.finalize_cancel_turn。"""
    turn_runner.driver = MagicMock()
    turn_runner.driver.cancel_turn = AsyncMock()
    turn_runner._history.finalize_cancel_turn = AsyncMock()

    call_order = []
    turn_runner.driver.cancel_turn.side_effect = lambda: call_order.append("driver")
    turn_runner._history.finalize_cancel_turn.side_effect = lambda: call_order.append("history")

    await turn_runner.handle_cancel_turn()

    turn_runner.driver.cancel_turn.assert_awaited_once()
    turn_runner._history.finalize_cancel_turn.assert_awaited_once()
    assert call_order == ["driver", "history"]
