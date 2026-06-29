"""compact 单元测试。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import OpenaiApiRole
from service import llmService
from service.agentService.compact import (
    calc_compact_trigger_tokens,
    calc_hard_limit_tokens,
    compact_messages,
    estimate_tokens,
    is_context_overflow_error,
)
from service.agentService.promptBuilder import (
    build_compact_instruction,
    build_compact_resume_prompt,
)
from util import llmApiUtil
from util.configTypes import LlmModelConfig, LlmContextConfig


def _make_llm_config(**overrides) -> LlmModelConfig:
    ctx_defaults = {
        "context_window_tokens": 32000,
        "reserve_output_tokens": 4096,
        "compact_trigger_ratio": 0.85,
        "compact_summary_max_tokens": 2048,
    }
    for k in list(overrides.keys()):
        if k in ctx_defaults:
            ctx_defaults[k] = overrides.pop(k)
    
    return LlmModelConfig(
        name=overrides.get("name", "test"),
        protocol="openai",
        context_config=LlmContextConfig(**ctx_defaults)
    )


# ─── calc_hard_limit_tokens ──────────────────────────────

@patch("service.agentService.compact.configUtil.get_app_config")
def test_calc_hard_limit_tokens_uses_builtin_default(mock_get_app_config):

    mock_app_config = MagicMock()
    mock_app_config.setting.context_config = LlmContextConfig()
    mock_get_app_config.return_value = mock_app_config
    cfg = _make_llm_config(name="gpt-4o", context_window_tokens=32000, reserve_output_tokens=4096)
    assert calc_hard_limit_tokens(cfg) == 123904


@patch("service.agentService.compact.configUtil.get_app_config")
def test_calc_hard_limit_tokens_falls_back_to_config(mock_get_app_config):

    mock_app_config = MagicMock()
    mock_app_config.setting.context_config = LlmContextConfig()
    mock_get_app_config.return_value = mock_app_config
    cfg = _make_llm_config(name="unknown-model-xyz", context_window_tokens=50000, reserve_output_tokens=2000)
    assert calc_hard_limit_tokens(cfg) == 48000


# ─── calc_compact_trigger_tokens ─────────────────────────

@patch("service.agentService.compact.configUtil.get_app_config")
def test_calc_compact_trigger_tokens_default(mock_get_app_config):

    mock_app_config = MagicMock()
    mock_app_config.setting.context_config = LlmContextConfig()
    mock_get_app_config.return_value = mock_app_config
    cfg = _make_llm_config(name="unknown-model", context_window_tokens=32000, reserve_output_tokens=4096, compact_trigger_ratio=0.85)
    # (32000 - 4096) * 0.85 = 23718.4 → floor = 23718
    result = calc_compact_trigger_tokens(cfg)
    assert result == 23718


@patch("service.agentService.compact.configUtil.get_app_config")
def test_calc_compact_trigger_tokens_known_model(mock_get_app_config):

    mock_app_config = MagicMock()
    mock_app_config.setting.context_config = LlmContextConfig()
    mock_get_app_config.return_value = mock_app_config
    cfg = _make_llm_config(name="gpt-4o", context_window_tokens=32000, reserve_output_tokens=4096, compact_trigger_ratio=0.85)
    # gpt-4o: (128000 - 4096) * 0.85 = 105318.4 → floor = 105318
    result = calc_compact_trigger_tokens(cfg)
    assert result == 105318


# ─── is_context_overflow_error ───────────────────────────

def test_is_context_overflow_error_matches_known_patterns():
    assert is_context_overflow_error(Exception("context_length_exceeded")) is True
    assert is_context_overflow_error(Exception("This model's maximum context length is 4096")) is True
    assert is_context_overflow_error(Exception("prompt is too long")) is True
    assert is_context_overflow_error(Exception("exceeds the context window")) is True
    assert is_context_overflow_error(Exception("too many tokens")) is True


def test_is_context_overflow_error_rejects_unrelated():
    assert is_context_overflow_error(Exception("rate limit exceeded")) is False
    assert is_context_overflow_error(Exception("invalid api key")) is False
    assert is_context_overflow_error(Exception("connection timeout")) is False


# ─── build_compact_instruction ────────────────────────────

def test_build_compact_instruction_includes_max_tokens():
    instruction = build_compact_instruction(max_tokens=2048)
    assert "2048" in instruction
    assert "总结" in instruction


def test_build_compact_instruction_is_concise():
    instruction = build_compact_instruction(max_tokens=1024)
    # 指令本身不应包含历史消息内容，只是一条简短指令
    assert len(instruction) < 500


def test_build_compact_resume_prompt_wraps_summary():
    context = build_compact_resume_prompt("  摘要内容  ")
    assert "以下是之前对话的压缩摘要" in context
    assert "摘要内容" in context


# ─── estimate_tokens ─────────────────────────────────────

def test_estimate_tokens_returns_positive_int():
    msgs = [llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "Hello world")]
    result = estimate_tokens("gpt-4o", msgs, system_prompt="You are helpful.")
    assert isinstance(result, int)
    assert result > 0


def test_estimate_tokens_with_empty_messages():
    result = estimate_tokens("gpt-4o", [], system_prompt="sys")
    assert isinstance(result, int)
    assert result > 0


# ─── compact_messages ─────────────────────────────────────

_INFER_PATCH = "service.agentService.compact.llmService.infer"


def _make_mock_response(content: str):
    """构造 mock LLM 响应。"""
    msg = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content=content,
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    resp.choices = [choice]
    return resp


def _make_tool() -> llmApiUtil.OpenAITool:
    return llmApiUtil.OpenAITool.model_validate({
        "type": "function",
        "function": {
            "name": "finish_action",
            "description": "结束行动",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    })


@pytest.mark.asyncio
async def test_compact_messages_success():
    """成功压缩，返回包含引导语的摘要。"""
    messages = [llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "历史消息")]
    mock_resp = _make_mock_response("这是摘要内容")
    tools = [_make_tool()]
    captured: dict[str, object] = {}

    async def _fake_infer(model, ctx):
        captured["model"] = model
        captured["ctx"] = ctx
        return llmService.InferResult.success(mock_resp)

    with patch(_INFER_PATCH, AsyncMock(side_effect=_fake_infer)):
        result = await compact_messages(messages, "system_prompt", "gpt-4o", tools=tools)

    assert result is not None
    assert "以下是之前对话的压缩摘要" in result
    assert "这是摘要内容" in result
    ctx = captured["ctx"]
    assert ctx.tools == tools
    assert ctx.tool_choice == "none"
    assert "不要使用任何工具" in ctx.messages[-1].content


@pytest.mark.asyncio
async def test_compact_messages_infer_failed():
    """LLM 推理失败，抛出 RuntimeError。"""
    messages = [llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "历史消息")]

    with patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.failure(Exception("API error")))):
        with pytest.raises(RuntimeError):
            await compact_messages(messages, "system_prompt", "gpt-4o")


@pytest.mark.asyncio
async def test_compact_messages_empty_content():
    """LLM 返回空内容，仍返回带引导语的空摘要。"""
    messages = [llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "历史消息")]
    mock_resp = _make_mock_response("")

    with patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(mock_resp))):
        result = await compact_messages(messages, "system_prompt", "gpt-4o")

    assert result is not None
    assert "以下是之前对话的压缩摘要" in result


@pytest.mark.asyncio
async def test_compact_messages_tool_calls_in_response_treated_as_failure():
    messages = [llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "历史消息")]
    tool_call_response = _make_mock_response("")
    tool_call_response.choices[0].message.tool_calls = [
        llmApiUtil.OpenAIToolCall.model_validate({
            "id": "call_1",
            "type": "function",
            "function": {"name": "finish_action", "arguments": "{}"},
        })
    ]

    with patch(_INFER_PATCH, AsyncMock(return_value=llmService.InferResult.success(tool_call_response))):
        with pytest.raises(RuntimeError, match="tool_calls"):
            await compact_messages(messages, "system_prompt", "gpt-4o", tools=[_make_tool()])


@pytest.mark.asyncio
async def test_compact_messages_exception():
    """LLM 调用抛出异常，异常向上传播（不再被吞咽）。"""
    messages = [llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "历史消息")]

    with patch(_INFER_PATCH, AsyncMock(side_effect=Exception("network error"))):
        with pytest.raises(Exception, match="network error"):
            await compact_messages(messages, "system_prompt", "gpt-4o")
