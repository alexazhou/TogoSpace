"""compactPolicy 单元测试。"""
import json

import pytest

from constants import OpenaiLLMApiRole
from service.agentService.compactPolicy import (
    build_compact_instruction,
    build_usage_payload,
    calc_compact_trigger_tokens,
    estimate_tokens,
    is_context_overflow_error,
    resolve_context_window,
    should_fail_after_compact,
    should_trigger_post_check,
    should_trigger_pre_check,
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


# ─── resolve_context_window ──────────────────────────────

def test_resolve_context_window_uses_builtin_default():
    cfg = _make_llm_config(context_window_tokens=32000)
    assert resolve_context_window("gpt-4o", cfg) == 128000


def test_resolve_context_window_falls_back_to_config():
    cfg = _make_llm_config(context_window_tokens=50000)
    assert resolve_context_window("unknown-model-xyz", cfg) == 50000


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


# ─── should_trigger_pre_check ────────────────────────────

def test_should_trigger_pre_check_true():
    assert should_trigger_pre_check(10000, 10000) is True
    assert should_trigger_pre_check(10001, 10000) is True


def test_should_trigger_pre_check_false():
    assert should_trigger_pre_check(9999, 10000) is False


# ─── should_trigger_post_check ───────────────────────────

def test_should_trigger_post_check_true_with_tool_calls():
    assert should_trigger_post_check(10000, 10000, has_tool_calls=True) is True


def test_should_trigger_post_check_false_without_tool_calls():
    assert should_trigger_post_check(10000, 10000, has_tool_calls=False) is False


def test_should_trigger_post_check_false_below_threshold():
    assert should_trigger_post_check(9999, 10000, has_tool_calls=True) is False


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


# ─── should_fail_after_compact ───────────────────────────

def test_should_fail_after_compact():
    assert should_fail_after_compact(10000, 10000) is True
    assert should_fail_after_compact(9999, 10000) is False


# ─── build_compact_instruction ────────────────────────────

def test_build_compact_instruction_includes_max_tokens():
    instruction = build_compact_instruction(max_tokens=2048)
    assert "2048" in instruction
    assert "总结" in instruction


def test_build_compact_instruction_is_concise():
    instruction = build_compact_instruction(max_tokens=1024)
    # 指令本身不应包含历史消息内容，只是一条简短指令
    assert len(instruction) < 500


# ─── build_usage_payload ─────────────────────────────────

def test_build_usage_payload_round_trips():
    payload_str = build_usage_payload(
        estimated_prompt_tokens=1000,
        prompt_tokens=950,
        completion_tokens=200,
        total_tokens=1150,
        pre_check_triggered=True,
        post_check_triggered=False,
        overflow_retry=False,
    )
    data = json.loads(payload_str)
    assert data["estimated_prompt_tokens"] == 1000
    assert data["prompt_tokens"] == 950
    assert data["pre_check_triggered"] is True
    assert data["overflow_retry"] is False


def test_build_usage_payload_allows_none_fields():
    payload_str = build_usage_payload(estimated_prompt_tokens=500)
    data = json.loads(payload_str)
    assert data["estimated_prompt_tokens"] == 500
    assert data["prompt_tokens"] is None


# ─── estimate_tokens ─────────────────────────────────────

def test_estimate_tokens_returns_positive_int():
    msgs = [llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "Hello world")]
    result = estimate_tokens("gpt-4o", msgs, system_prompt="You are helpful.")
    assert isinstance(result, int)
    assert result > 0


def test_estimate_tokens_with_empty_messages():
    result = estimate_tokens("gpt-4o", [], system_prompt="sys")
    assert isinstance(result, int)
    assert result > 0
