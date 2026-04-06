"""_infer() compact 流程单元测试：pre-check / post-check / overflow retry。

通过 mock llmService.infer、compactPolicy.estimate_tokens 和 configUtil，
隔离测试 AgentTurnRunner._infer() 中的各条 compact 分支。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import llmService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall, OpenaiLLMApiRole

# ────── helpers ──────


def _make_mock_response(content="ok", tool_calls=None, usage=None):
    """构造 mock OpenAI 响应对象。"""
    msg = OpenAIMessage(
        role=OpenaiLLMApiRole.ASSISTANT,
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
    """构造 mock usage 对象。"""
    u = MagicMock()
    u.prompt_tokens = prompt
    u.completion_tokens = completion
    u.total_tokens = total
    return u


def _make_tool_calls():
    return [OpenAIToolCall(id="call_1", function={"name": "foo", "arguments": "{}"})]


def _make_history_item(item_id=1):
    item = MagicMock(spec=GtAgentHistory)
    item.id = item_id
    return item


def _make_runner_and_history():
    """构造 TurnRunner + mock history。"""
    gt_agent = GtAgent(id=1, team_id=1, name="TestBot", role_template_id=1, model="mock-model")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        max_function_calls=5,
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    history = MagicMock()
    history.assert_infer_ready = MagicMock()
    history.build_infer_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiLLMApiRole.USER, content="hello"),
    ])
    history.build_compact_source_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiLLMApiRole.USER, content="hello"),
    ])
    history.export_openai_tools = MagicMock(return_value=[])
    history.append_stage_init = AsyncMock(return_value=_make_history_item())
    history.finalize_history_item = AsyncMock()
    history.append_history_message = AsyncMock(return_value=_make_history_item(2))
    history.drop_messages_before_latest_compact = MagicMock()
    runner._history = history

    return runner, history


def _mock_config():
    """构造 mock configUtil.get_app_config() 返回值。"""
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


# token_compact_threshold = floor((32000 - 4096) * 0.85) = 23718
TRIGGER_TOKENS = 23718

_CONFIG_PATCH = "service.agentService.agentTurnRunner.configUtil.get_app_config"
_INFER_PATCH = "service.agentService.agentTurnRunner.llmService.infer"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compactPolicy.estimate_tokens"


# ────── Tests ──────


@pytest.mark.asyncio
async def test_infer_normal_no_compact():
    """正常推理：估算 token 低于阈值，无 compact 触发。"""
    runner, history = _make_runner_and_history()
    resp = _make_mock_response(content="回答")
    result = llmService.InferResult.success(resp)

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=result)),
        patch(_ESTIMATE_PATCH, return_value=1000),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "回答"
    history.finalize_history_item.assert_called_once()
    call_kwargs = history.finalize_history_item.call_args[1]
    assert call_kwargs["status"] == AgentHistoryStatus.SUCCESS
    # 未触发 compact
    history.drop_messages_before_latest_compact.assert_not_called()


@pytest.mark.asyncio
async def test_infer_pre_check_triggers_compact():
    """Pre-check 触发：估算 token ≥ 阈值 → compact(_infer) → 正常推理。"""
    runner, history = _make_runner_and_history()
    resp = _make_mock_response(content="压缩后的回答")
    compact_resp = _make_mock_response(content="对话摘要")

    # 1st: main pre-check (over), 2nd: compact's _infer (ignored), 3rd: re-estimate (under)
    estimate_calls = iter([TRIGGER_TOKENS + 100, 500, 5000])

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.success(compact_resp),  # compact's _infer
            llmService.InferResult.success(resp),           # main infer
        ])),
        patch(_ESTIMATE_PATCH, side_effect=estimate_calls),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "压缩后的回答"
    history.drop_messages_before_latest_compact.assert_called_once()


@pytest.mark.asyncio
async def test_infer_pre_check_still_over_after_compact():
    """Pre-check compact 后仍超限 → 抛出 RuntimeError。"""
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="摘要")

    # 1st: pre-check (over), 2nd: compact's _infer, 3rd: re-estimate (still over)
    estimate_calls = iter([TRIGGER_TOKENS + 100, 500, TRIGGER_TOKENS + 50])

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=estimate_calls),
    ):
        with pytest.raises(RuntimeError, match="compact 后仍超限"):
            await runner._infer(tools=None)


@pytest.mark.asyncio
async def test_infer_post_check_triggers_compact_with_tool_calls():
    """Post-check 触发：实测 usage 超阈值 + 有 tool_calls → compact + re-infer。"""
    runner, history = _make_runner_and_history()
    usage = _make_usage(prompt=TRIGGER_TOKENS + 100, completion=50, total=TRIGGER_TOKENS + 150)
    first_resp = _make_mock_response(content=None, tool_calls=_make_tool_calls(), usage=usage)
    compact_resp = _make_mock_response(content="摘要")
    re_infer_resp = _make_mock_response(content="重新推理")

    # 1st: main pre-check (ok), 2nd: compact's _infer, 3rd: post-check re-estimate
    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.success(first_resp),     # first infer (triggers post-check)
            llmService.InferResult.success(compact_resp),   # compact's _infer
            llmService.InferResult.success(re_infer_resp),  # re-infer
        ])),
        patch(_ESTIMATE_PATCH, return_value=5000),  # always low
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "重新推理"
    history.drop_messages_before_latest_compact.assert_called_once()


@pytest.mark.asyncio
async def test_infer_post_check_no_tool_calls_skips():
    """Post-check 不触发：usage 超阈值但无 tool_calls → 不 compact，正常返回。"""
    runner, history = _make_runner_and_history()
    usage = _make_usage(prompt=TRIGGER_TOKENS + 100)
    resp = _make_mock_response(content="直接回答", tool_calls=None, usage=usage)

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=5000),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "直接回答"
    history.drop_messages_before_latest_compact.assert_not_called()


@pytest.mark.asyncio
async def test_infer_overflow_triggers_compact_retry():
    """Overflow 触发：LLM 返回上下文溢出错误 → compact(_infer) → retry 成功。"""
    runner, history = _make_runner_and_history()
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")
    compact_resp = _make_mock_response(content="摘要")
    retry_resp = _make_mock_response(content="重试成功")

    # 1st: main pre-check (ok), 2nd: compact's _infer, 3rd: retry estimate
    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.failure(overflow_error),  # first infer fails
            llmService.InferResult.success(compact_resp),    # compact's _infer
            llmService.InferResult.success(retry_resp),      # retry
        ])),
        patch(_ESTIMATE_PATCH, return_value=5000),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "重试成功"
    history.drop_messages_before_latest_compact.assert_called_once()


@pytest.mark.asyncio
async def test_infer_overflow_after_precheck_no_retry():
    """Pre-check 已触发过 → overflow 不再 retry，直接失败。"""
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="摘要")
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")

    # 1st: pre-check (over), 2nd: compact's _infer, 3rd: re-estimate (ok)
    estimate_calls = iter([TRIGGER_TOKENS + 100, 500, 5000])

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.success(compact_resp),    # compact's _infer (pre-check)
            llmService.InferResult.failure(overflow_error),  # main infer fails
        ])),
        patch(_ESTIMATE_PATCH, side_effect=estimate_calls),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._infer(tools=None)

    # compact 只因 pre-check 调了一次
    history.drop_messages_before_latest_compact.assert_called_once()


@pytest.mark.asyncio
async def test_infer_non_overflow_failure_raises():
    """非 overflow 失败 → 直接 RuntimeError，不尝试 compact。"""
    runner, history = _make_runner_and_history()
    generic_error = Exception("rate limit exceeded")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.failure(generic_error))),
        patch(_ESTIMATE_PATCH, return_value=5000),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._infer(tools=None)

    history.drop_messages_before_latest_compact.assert_not_called()


@pytest.mark.asyncio
async def test_infer_overflow_compact_still_over_fails():
    """Overflow compact 后仍超限 → 失败。"""
    runner, history = _make_runner_and_history()
    overflow_error = Exception("context_length_exceeded: max is 32000")
    compact_resp = _make_mock_response(content="摘要")

    # 1st: main pre-check (ok), 2nd: compact's _infer, 3rd: retry estimate (still high)
    estimate_calls = iter([5000, 500, TRIGGER_TOKENS + 100])

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.failure(overflow_error),
            llmService.InferResult.success(compact_resp),
        ])),
        patch(_ESTIMATE_PATCH, side_effect=estimate_calls),
    ):
        with pytest.raises(RuntimeError, match="overflow compact 后仍超限"):
            await runner._infer(tools=None)

    # finalize 被调两次：compact 的 _infer(SUCCESS) + 主流程 overflow(FAILED)
    assert history.finalize_history_item.call_count == 2
    last_call_kwargs = history.finalize_history_item.call_args_list[-1][1]
    assert last_call_kwargs["status"] == AgentHistoryStatus.FAILED


@pytest.mark.asyncio
async def test_infer_usage_recorded_in_finalize():
    """验证 usage_json 正确传递到 finalize_history_item。"""
    runner, history = _make_runner_and_history()
    usage = _make_usage(prompt=1000, completion=200, total=1200)
    resp = _make_mock_response(content="ok", usage=usage)

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=500),
    ):
        await runner._infer(tools=None)

    call_kwargs = history.finalize_history_item.call_args[1]
    usage_data = json.loads(call_kwargs["usage_json"])
    assert usage_data["prompt_tokens"] == 1000
    assert usage_data["completion_tokens"] == 200
    assert usage_data["total_tokens"] == 1200
    assert usage_data["estimated_prompt_tokens"] == 500


@pytest.mark.asyncio
async def test_infer_resume_item_skips_last_message():
    """resume_item 不为 None → messages 去掉最后一条。"""
    runner, history = _make_runner_and_history()
    history.build_infer_messages.return_value = [
        OpenAIMessage(role=OpenaiLLMApiRole.USER, content="msg1"),
        OpenAIMessage(role=OpenaiLLMApiRole.ASSISTANT, content="msg2"),
    ]
    resp = _make_mock_response(content="续跑回答")
    resume_item = _make_history_item(99)

    infer_mock = AsyncMock(return_value=llmService.InferResult.success(resp))
    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, infer_mock),
        patch(_ESTIMATE_PATCH, return_value=500),
    ):
        msg = await runner._infer(tools=None, resume_item=resume_item)

    assert msg.content == "续跑回答"
    # 检查传给 llmService.infer 的消息只有第一条
    call_args = infer_mock.call_args
    ctx = call_args[0][1]  # second positional arg is context
    assert len(ctx.messages) == 1
    assert ctx.messages[0].content == "msg1"
    # assert_infer_ready 不应被调用
    history.assert_infer_ready.assert_not_called()


@pytest.mark.asyncio
async def test_execute_compact_skips_when_no_source():
    """无可压缩消息时 _execute_compact 跳过。"""
    runner, history = _make_runner_and_history()
    history.build_compact_source_messages.return_value = []

    with patch(_CONFIG_PATCH, return_value=_mock_config()):
        await runner._execute_compact()

    history.drop_messages_before_latest_compact.assert_not_called()


@pytest.mark.asyncio
async def test_execute_compact_tags_upfront_and_trims():
    """_execute_compact：直接带 COMPACT_CMD tag 写入指令 → _infer 推理 → 裁剪。"""
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="压缩摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, return_value=500),
    ):
        await runner._execute_compact()

    history.drop_messages_before_latest_compact.assert_called_once()
    # 指令消息直接带 COMPACT_CMD tag 写入
    instruction_call = history.append_history_message.call_args_list[0]
    assert instruction_call[1]["tags"] == [AgentHistoryTag.COMPACT_CMD]


@pytest.mark.asyncio
async def test_execute_compact_failure_raises():
    """compact 推理失败 → RuntimeError。"""
    runner, history = _make_runner_and_history()
    error = Exception("LLM service unavailable")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.failure(error))),
        patch(_ESTIMATE_PATCH, return_value=500),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._execute_compact()
