"""compactPolicy 单元测试。"""
import pytest

from constants import OpenaiApiRole
from service.agentService.promptBuilder import (
    build_compact_instruction,
    build_compact_resume_prompt,
)
from service.agentService.compactPolicy import (
    calc_compact_trigger_tokens,
    calc_hard_limit_tokens,
    estimate_tokens,
    is_context_overflow_error,
)
from util import llmApiUtil
from util.configTypes import LlmServiceConfig


def _make_llm_config(**overrides) -> LlmServiceConfig:
    defaults = {
        "name": "test",
        "base_url": "http://localhost",
        "api_key": "key",
        "type": "openai-compatible",
        "context_window_tokens": 32000,
        "reserve_output_tokens": 4096,
        "compact_trigger_ratio": 0.85,
        "compact_summary_max_tokens": 2048,
    }
    defaults.update(overrides)
    return LlmServiceConfig(**defaults)


# ─── calc_hard_limit_tokens ──────────────────────────────

def test_calc_hard_limit_tokens_uses_builtin_default():
    cfg = _make_llm_config(context_window_tokens=32000, reserve_output_tokens=4096)
    assert calc_hard_limit_tokens("gpt-4o", cfg) == 123904


def test_calc_hard_limit_tokens_falls_back_to_config():
    cfg = _make_llm_config(context_window_tokens=50000, reserve_output_tokens=2000)
    assert calc_hard_limit_tokens("unknown-model-xyz", cfg) == 48000


# ─── calc_compact_trigger_tokens ─────────────────────────

def test_calc_compact_trigger_tokens_default():
    cfg = _make_llm_config(context_window_tokens=32000, reserve_output_tokens=4096, compact_trigger_ratio=0.85)
    # (32000 - 4096) * 0.85 = 23718.4 → floor = 23718
    result = calc_compact_trigger_tokens("unknown-model", cfg)
    assert result == 23718


def test_calc_compact_trigger_tokens_known_model():
    cfg = _make_llm_config(context_window_tokens=32000, reserve_output_tokens=4096, compact_trigger_ratio=0.85)
    # gpt-4o: (128000 - 4096) * 0.85 = 105318.4 → floor = 105318
    result = calc_compact_trigger_tokens("gpt-4o", cfg)
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
