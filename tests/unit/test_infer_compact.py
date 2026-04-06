"""_infer() 与 compact 流程单元测试。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import CompactPlan
from service import llmService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall, OpenaiLLMApiRole


def _make_mock_response(content="ok", tool_calls=None, usage=None):
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
        max_function_calls=5,
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    history = MagicMock()
    history.assert_infer_ready = MagicMock()
    history.build_infer_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiLLMApiRole.USER, content="hello"),
    ])
    history.get_pending_infer_item = MagicMock(return_value=None)
    history.build_compact_plan = MagicMock(return_value=CompactPlan(
        source_messages=[OpenAIMessage(role=OpenaiLLMApiRole.USER, content="hello")],
        insert_seq=1,
    ))
    history.append_stage_init = AsyncMock(return_value=_make_history_item())
    history.finalize_history_item = AsyncMock()
    history.insert_history_message_at_seq = AsyncMock(return_value=_make_history_item(2))
    history.trim_to_compact_window = MagicMock()
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
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compactPolicy.estimate_tokens"


@pytest.mark.asyncio
async def test_infer_normal_no_compact():
    runner, history = _make_runner_and_history()
    resp = _make_mock_response(content="回答")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "回答"
    history.append_stage_init.assert_called_once()
    history.finalize_history_item.assert_called_once()
    history.trim_to_compact_window.assert_not_called()


@pytest.mark.asyncio
async def test_infer_reuses_pending_infer_item():
    runner, history = _make_runner_and_history()
    pending_item = _make_history_item(99)
    history.get_pending_infer_item.return_value = pending_item
    resp = _make_mock_response(content="续跑回答")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=500),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "续跑回答"
    history.append_stage_init.assert_not_called()
    call_kwargs = history.finalize_history_item.call_args[1]
    assert call_kwargs["history_id"] == 99


@pytest.mark.asyncio
async def test_infer_pre_check_triggers_compact():
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="摘要")
    main_resp = _make_mock_response(content="压缩后的回答")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.success(compact_resp),
            llmService.InferResult.success(main_resp),
        ])),
        patch(_ESTIMATE_PATCH, side_effect=[TRIGGER_TOKENS + 100, 5000]),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "压缩后的回答"
    history.trim_to_compact_window.assert_called_once()
    usage_data = json.loads(history.finalize_history_item.call_args[1]["usage_json"])
    assert usage_data["pre_check_triggered"] is True


@pytest.mark.asyncio
async def test_infer_pre_check_still_over_after_compact():
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
        patch(_ESTIMATE_PATCH, side_effect=[TRIGGER_TOKENS + 100, HARD_LIMIT_TOKENS + 10]),
    ):
        with pytest.raises(RuntimeError, match="compact 后仍超限"):
            await runner._infer(tools=None)

    history.append_stage_init.assert_not_called()


@pytest.mark.asyncio
async def test_infer_overflow_triggers_compact_retry():
    runner, history = _make_runner_and_history()
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")
    compact_resp = _make_mock_response(content="摘要")
    retry_resp = _make_mock_response(content="重试成功")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.failure(overflow_error),
            llmService.InferResult.success(compact_resp),
            llmService.InferResult.success(retry_resp),
        ])),
        patch(_ESTIMATE_PATCH, side_effect=[5000, 5000]),
    ):
        msg = await runner._infer(tools=None)

    assert msg.content == "重试成功"
    usage_data = json.loads(history.finalize_history_item.call_args[1]["usage_json"])
    assert usage_data["overflow_retry"] is True
    history.trim_to_compact_window.assert_called_once()


@pytest.mark.asyncio
async def test_infer_overflow_after_precheck_no_retry():
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="摘要")
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.success(compact_resp),
            llmService.InferResult.failure(overflow_error),
        ])),
        patch(_ESTIMATE_PATCH, side_effect=[TRIGGER_TOKENS + 100, 5000]),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._infer(tools=None)

    history.trim_to_compact_window.assert_called_once()


@pytest.mark.asyncio
async def test_infer_non_overflow_failure_raises():
    runner, history = _make_runner_and_history()
    generic_error = Exception("rate limit exceeded")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.failure(generic_error))),
        patch(_ESTIMATE_PATCH, return_value=5000),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败"):
            await runner._infer(tools=None)

    history.trim_to_compact_window.assert_not_called()


@pytest.mark.asyncio
async def test_infer_overflow_compact_still_over_fails():
    runner, history = _make_runner_and_history()
    overflow_error = Exception("context_length_exceeded: max is 32000")
    compact_resp = _make_mock_response(content="摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(side_effect=[
            llmService.InferResult.failure(overflow_error),
            llmService.InferResult.success(compact_resp),
        ])),
        patch(_ESTIMATE_PATCH, side_effect=[5000, HARD_LIMIT_TOKENS + 10]),
    ):
        with pytest.raises(RuntimeError, match="overflow compact 后仍超限"):
            await runner._infer(tools=None)

    history.finalize_history_item.assert_called_once()
    assert history.finalize_history_item.call_args[1]["status"] == AgentHistoryStatus.FAILED


@pytest.mark.asyncio
async def test_infer_usage_recorded_in_finalize():
    runner, history = _make_runner_and_history()
    usage = _make_usage(prompt=1000, completion=200, total=1200)
    resp = _make_mock_response(content="ok", usage=usage)

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=500),
    ):
        await runner._infer(tools=None)

    usage_data = json.loads(history.finalize_history_item.call_args[1]["usage_json"])
    assert usage_data["estimated_prompt_tokens"] == 500
    assert usage_data["prompt_tokens"] == 1000
    assert usage_data["completion_tokens"] == 200
    assert usage_data["total_tokens"] == 1200


@pytest.mark.asyncio
async def test_execute_compact_skips_when_no_source():
    runner, history = _make_runner_and_history()
    history.build_compact_plan.return_value = CompactPlan(source_messages=[], insert_seq=None)

    with patch(_CONFIG_PATCH, return_value=_mock_config()):
        await runner._execute_compact()

    history.insert_history_message_at_seq.assert_not_called()
    history.trim_to_compact_window.assert_not_called()


@pytest.mark.asyncio
async def test_execute_compact_inserts_cmd_summary_context_and_trims():
    runner, history = _make_runner_and_history()
    compact_resp = _make_mock_response(content="压缩摘要")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(compact_resp))),
    ):
        await runner._execute_compact()

    assert history.insert_history_message_at_seq.await_count == 3

    cmd_call = history.insert_history_message_at_seq.await_args_list[0]
    assert cmd_call.kwargs["insert_seq"] == 1
    assert cmd_call.kwargs["tags"] == [AgentHistoryTag.COMPACT_CMD]

    summary_call = history.insert_history_message_at_seq.await_args_list[1]
    assert summary_call.kwargs["insert_seq"] == 2
    assert summary_call.kwargs["stage"] == AgentHistoryStage.INFER
    assert summary_call.kwargs["message"].content == "压缩摘要"

    context_call = history.insert_history_message_at_seq.await_args_list[2]
    assert context_call.kwargs["insert_seq"] == 3
    assert context_call.kwargs["stage"] == AgentHistoryStage.INPUT
    assert "以下是之前对话的压缩摘要" in context_call.kwargs["message"].content

    history.trim_to_compact_window.assert_called_once()


@pytest.mark.asyncio
async def test_execute_compact_failure_raises():
    runner, history = _make_runner_and_history()
    error = Exception("LLM service unavailable")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.failure(error))),
    ):
        with pytest.raises(RuntimeError, match="LLM 推理失败\\(compact\\)"):
            await runner._execute_compact()

    assert history.insert_history_message_at_seq.await_count == 1
    history.trim_to_compact_window.assert_not_called()
