"""_infer_to_item() 与 compact 流程单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStatus, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import CompactPlan
from service import llmService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall, OpenaiApiRole


def _make_mock_response(content="ok", tool_calls=None, usage=None):
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content=content,
        tool_calls=tool_calls,
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _make_usage(prompt=100, completion=50, total=150):
    usage = MagicMock()
    usage.prompt_tokens = prompt
    usage.completion_tokens = completion
    usage.total_tokens = total
    return usage


def _make_history_item(item_id=1):
    item = MagicMock(spec=GtAgentHistory)
    item.id = item_id
    return item


def _make_runner_and_history():
    gt_agent = GtAgent(id=1, team_id=1, name="TestBot", role_template_id=1, model="mock-model")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    history = MagicMock()
    history.is_infer_ready = MagicMock(return_value=True)
    history.build_infer_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiApiRole.USER, content="hello"),
    ])
    history.build_compact_plan = MagicMock(return_value=CompactPlan(
        source_messages=[OpenAIMessage(role=OpenaiApiRole.USER, content="hello")],
        insert_seq=1,
    ))
    history.append_history_init_item = AsyncMock(return_value=_make_history_item())
    history.finalize_history_item = AsyncMock()
    history.append_history_message = AsyncMock(return_value=_make_history_item(2))
    history.insert_compact_summary = AsyncMock(return_value=_make_history_item(2))
    runner._history = history
    return runner, history


def _mock_config():
    llm_cfg = MagicMock()
    llm_cfg.context_window_tokens = 32000
    llm_cfg.reserve_output_tokens = 4096
    llm_cfg.compact_trigger_ratio = 0.85
    llm_cfg.compact_summary_max_tokens = 2048
    llm_cfg.model = "mock-model"
    setting = MagicMock()
    setting.current_llm_service = llm_cfg
    app_config = MagicMock()
    app_config.setting = setting
    return app_config


TRIGGER_TOKENS = 23718
HARD_LIMIT_TOKENS = 27904

_CONFIG_PATCH = "service.agentService.agentTurnRunner.configUtil.get_app_config"
_INFER_PATCH = "service.agentService.agentTurnRunner.llmService.infer"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"
_ACTIVITY_PATCH = "service.agentService.agentTurnRunner.agentActivityService"


def _mock_activity_service():
    """返回一个 mock agentActivityService，add_activity 返回带 id 的 mock。"""
    mock_svc = MagicMock()
    mock_activity = MagicMock()
    mock_activity.id = 1
    mock_svc.add_activity = AsyncMock(return_value=mock_activity)
    mock_svc.update_activity_progress = AsyncMock(return_value=mock_activity)
    return mock_svc


@pytest.mark.asyncio
async def test_infer_normal_no_compact():
    runner, history = _make_runner_and_history()
    resp = _make_mock_response(content="回答")
    output_item = _make_history_item()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        msg = await runner._infer_to_item(output_item, tools=[])

    assert msg.content == "回答"
    history.finalize_history_item.assert_called_once()


# ─── REASONING 和 CHAT_REPLY 活动记录触发测试 ──


@pytest.mark.asyncio
async def test_infer_creates_reasoning_and_chat_reply_activities():
    """推理返回 reasoning_content 和 content 时，应触发 REASONING 和 CHAT_REPLY 活动记录。"""
    runner, history = _make_runner_and_history()
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1

    # 构造包含 reasoning_content 和 content 的响应
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="这是直接发言内容",
        reasoning_content="这是思考过程",
        tool_calls=None,
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = _make_usage()
    output_item = _make_history_item()

    mock_activity_svc = _mock_activity_service()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, mock_activity_svc),
    ):
        result_msg = await runner._infer_to_item(output_item, tools=[])

    assert result_msg.content == "这是直接发言内容"
    assert result_msg.reasoning_content == "这是思考过程"

    # 验证 add_activity 被调用三次：LLM_INFER + REASONING + CHAT_REPLY
    assert mock_activity_svc.add_activity.await_count == 3

    # 检查调用参数中的 activity_type
    call_types = [call.kwargs.get("activity_type") for call in mock_activity_svc.add_activity.call_args_list]
    from constants import AgentActivityType
    assert AgentActivityType.LLM_INFER in call_types
    assert AgentActivityType.REASONING in call_types
    assert AgentActivityType.CHAT_REPLY in call_types

    # 验证 detail 内容
    reasoning_call = next(c for c in mock_activity_svc.add_activity.call_args_list
                          if c.kwargs.get("activity_type") == AgentActivityType.REASONING)
    assert reasoning_call.kwargs.get("detail") == "这是思考过程"

    chat_reply_call = next(c for c in mock_activity_svc.add_activity.call_args_list
                           if c.kwargs.get("activity_type") == AgentActivityType.CHAT_REPLY)
    assert chat_reply_call.kwargs.get("detail") == "这是直接发言内容"


@pytest.mark.asyncio
async def test_infer_creates_chat_reply_even_with_tool_calls():
    """推理返回 content 且有 tool_calls 时，仍应触发 CHAT_REPLY 活动记录。"""
    runner, history = _make_runner_and_history()
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1

    # 构造包含 content 和 tool_calls 的响应（无 reasoning_content）
    tool_call = OpenAIToolCall(
        id="tc-123",
        type="function",
        function={"name": "execute_bash", "arguments": '{"command": "ls"}'},
    )
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="执行工具前的说明",
        reasoning_content=None,
        tool_calls=[tool_call],
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls"
    resp.choices = [choice]
    resp.usage = _make_usage()
    output_item = _make_history_item()

    mock_activity_svc = _mock_activity_service()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, mock_activity_svc),
    ):
        result_msg = await runner._infer_to_item(output_item, tools=[])

    assert result_msg.content == "执行工具前的说明"
    assert result_msg.tool_calls is not None

    from constants import AgentActivityType
    call_types = [call.kwargs.get("activity_type") for call in mock_activity_svc.add_activity.call_args_list]

    # 应包含 CHAT_REPLY（即使有 tool_calls）
    assert AgentActivityType.CHAT_REPLY in call_types
    # 不应包含 REASONING（reasoning_content 为空）
    assert AgentActivityType.REASONING not in call_types


@pytest.mark.asyncio
async def test_infer_skips_activities_for_empty_content():
    """推理返回空 reasoning_content 或空 content 时，不应触发对应活动记录。"""
    runner, history = _make_runner_and_history()
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1

    # 构造空内容的响应
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="",           # 空
        reasoning_content="   ",  # 仅空白
        tool_calls=None,
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = _make_usage()
    output_item = _make_history_item()

    mock_activity_svc = _mock_activity_service()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, mock_activity_svc),
    ):
        await runner._infer_to_item(output_item, tools=[])

    from constants import AgentActivityType
    call_types = [call.kwargs.get("activity_type") for call in mock_activity_svc.add_activity.call_args_list]

    # 只应有 LLM_INFER，不应包含 REASONING 或 CHAT_REPLY
    assert AgentActivityType.LLM_INFER in call_types
    assert AgentActivityType.REASONING not in call_types
    assert AgentActivityType.CHAT_REPLY not in call_types
    history.insert_compact_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_infer_reuses_pending_infer_item():
    runner, history = _make_runner_and_history()
    pending_item = _make_history_item(99)
    resp = _make_mock_response(content="续跑回答")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=500),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        msg = await runner._infer_to_item(pending_item, tools=[])

    assert msg.content == "续跑回答"
    call_kwargs = history.finalize_history_item.call_args[1]
    assert call_kwargs["history_id"] == 99


@pytest.mark.asyncio
async def test_infer_pre_check_triggers_compact():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    compact_resp = _make_mock_response(content="摘要")
    main_resp = _make_mock_response(content="压缩后的回答")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.success(main_resp),
        ])),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[TRIGGER_TOKENS + 100, 5000]),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        msg = await runner._infer_to_item(output_item, tools=[])

    assert msg.content == "压缩后的回答"
    history.insert_compact_summary.assert_awaited_once()
    usage_data = history.finalize_history_item.call_args[1]["usage"]
    assert usage_data.compact_stage == "pre"


@pytest.mark.asyncio
async def test_infer_pre_check_still_over_after_compact():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    compact_resp = _make_mock_response(content="摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[TRIGGER_TOKENS + 100, HARD_LIMIT_TOKENS + 10]),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        with pytest.raises(RuntimeError, match="compact 后仍超限"):
            await runner._infer_to_item(output_item, tools=[])


@pytest.mark.asyncio
async def test_infer_pre_check_compact_failure_raises():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    history.build_compact_plan.return_value = None

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_ESTIMATE_PATCH, return_value=TRIGGER_TOKENS + 100),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        with pytest.raises(RuntimeError, match="pre-check compact 失败"):
            await runner._infer_to_item(output_item, tools=[])

    history.insert_compact_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_infer_post_check_triggers_compact():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    usage = _make_usage(prompt=TRIGGER_TOKENS + 100, completion=200, total=TRIGGER_TOKENS + 300)
    main_resp = _make_mock_response(content="正常回答", usage=usage)
    compact_resp = _make_mock_response(content="摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(main_resp))),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[5000, 4000]),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        msg = await runner._infer_to_item(output_item, tools=[])

    assert msg.content == "正常回答"
    history.insert_compact_summary.assert_awaited_once()
    usage_data = history.finalize_history_item.call_args[1]["usage"]
    assert usage_data.compact_stage == "post"
    assert usage_data.prompt_tokens == TRIGGER_TOKENS + 100


@pytest.mark.asyncio
async def test_infer_post_check_compact_failure_raises():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    usage = _make_usage(prompt=TRIGGER_TOKENS + 100, completion=200, total=TRIGGER_TOKENS + 300)
    main_resp = _make_mock_response(content="正常回答", usage=usage)
    history.build_compact_plan.return_value = None

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(main_resp))),
        patch(_ESTIMATE_PATCH, return_value=5000),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        with pytest.raises(RuntimeError, match="post-check compact 失败"):
            await runner._infer_to_item(output_item, tools=[])

    history.insert_compact_summary.assert_not_awaited()
    assert history.finalize_history_item.await_count == 2
    first_call = history.finalize_history_item.await_args_list[0]
    second_call = history.finalize_history_item.await_args_list[1]
    assert first_call.kwargs["status"] == AgentHistoryStatus.SUCCESS
    assert first_call.kwargs["usage"].compact_stage == "none"
    assert second_call.kwargs["status"] == AgentHistoryStatus.SUCCESS
    assert second_call.kwargs["usage"].compact_stage == "post"


@pytest.mark.asyncio
async def test_infer_overflow_triggers_compact_retry():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")
    compact_resp = _make_mock_response(content="摘要")
    retry_resp = _make_mock_response(content="重试成功")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.failure(overflow_error),
            llmService.InferResult.success(retry_resp),
        ])),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[5000, 5000]),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        msg = await runner._infer_to_item(output_item, tools=[])

    assert msg.content == "重试成功"
    usage_data = history.finalize_history_item.call_args[1]["usage"]
    assert usage_data.overflow_retry is True
    history.insert_compact_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_infer_overflow_after_precheck_no_retry():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    compact_resp = _make_mock_response(content="摘要")
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.failure(overflow_error))),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[TRIGGER_TOKENS + 100, 5000]),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._infer_to_item(output_item, tools=[])

    history.insert_compact_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_infer_non_overflow_failure_raises():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    generic_error = Exception("rate limit exceeded")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.failure(generic_error))),
        patch(_ESTIMATE_PATCH, return_value=5000),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._infer_to_item(output_item, tools=[])

    history.insert_compact_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_infer_overflow_compact_still_over_fails():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    overflow_error = Exception("context_length_exceeded: max is 32000")
    compact_resp = _make_mock_response(content="摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.failure(overflow_error))),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[5000, HARD_LIMIT_TOKENS + 10]),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        with pytest.raises(RuntimeError, match="overflow compact 后仍超限"):
            await runner._infer_to_item(output_item, tools=[])

    history.finalize_history_item.assert_called_once()
    assert history.finalize_history_item.call_args[1]["status"] == AgentHistoryStatus.FAILED


@pytest.mark.asyncio
async def test_infer_usage_recorded_in_finalize():
    runner, history = _make_runner_and_history()
    output_item = _make_history_item()
    usage = _make_usage(prompt=1000, completion=200, total=1200)
    resp = _make_mock_response(content="ok", usage=usage)

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=500),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        await runner._infer_to_item(output_item, tools=[])

    usage_data = history.finalize_history_item.call_args[1]["usage"]
    assert usage_data.estimated_prompt_tokens == 500
    assert usage_data.compact_stage == "none"
    assert usage_data.prompt_tokens == 1000
    assert usage_data.completion_tokens == 200
    assert usage_data.total_tokens == 1200


@pytest.mark.asyncio
async def test_execute_compact_skips_when_no_source():
    runner, history = _make_runner_and_history()
    history.build_compact_plan.return_value = None

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        result = await runner._execute_compact()

    assert result is False
    history.insert_compact_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_compact_inserts_summary_and_trims():
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="压缩摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        result = await runner._execute_compact()

    assert result is True
    history.insert_compact_summary.assert_awaited_once()

    call = history.insert_compact_summary.call_args_list[0]
    assert call.kwargs["seq"] == 1
    assert "以下是之前对话的压缩摘要" in call.args[0].content


@pytest.mark.asyncio
async def test_execute_compact_failure_returns_false():
    runner, history = _make_runner_and_history()
    error = Exception("LLM service unavailable")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.failure(error))),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        result = await runner._execute_compact()

    assert result is False
    history.insert_compact_summary.assert_not_awaited()
