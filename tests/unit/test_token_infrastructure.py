"""Phase 1 基础设施单元测试：配置字段、usage 解析、DB 字段。"""
import pytest

from util.configTypes import LlmServiceConfig
from util.llmApiUtil import OpenAIResponse, OpenAIUsage, OpenAIChoice, OpenAIMessage
from constants import OpenaiApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.historyUsage import HistoryUsage


# ─── LlmServiceConfig token 预算字段 ────────────────────

def test_llm_config_default_token_budget_fields():
    cfg = LlmServiceConfig(
        name="test", base_url="http://x", api_key="k", type="openai-compatible"
    )
    assert cfg.context_window_tokens == 65536
    assert cfg.reserve_output_tokens == 8192
    assert cfg.compact_trigger_ratio == 0.85
    assert cfg.compact_summary_max_tokens == 2048


def test_llm_config_custom_token_budget_fields():
    cfg = LlmServiceConfig(
        name="test", base_url="http://x", api_key="k", type="openai-compatible",
        context_window_tokens=128000,
        reserve_output_tokens=8192,
        compact_trigger_ratio=0.9,
        compact_summary_max_tokens=4096,
    )
    assert cfg.context_window_tokens == 128000
    assert cfg.reserve_output_tokens == 8192
    assert cfg.compact_trigger_ratio == 0.9
    assert cfg.compact_summary_max_tokens == 4096


def test_llm_config_trigger_ratio_validation():
    with pytest.raises(Exception):
        LlmServiceConfig(
            name="test", base_url="http://x", api_key="k", type="openai-compatible",
            compact_trigger_ratio=1.5,
        )


def test_llm_config_extra_ignore_preserves_token_fields():
    """确认 extra='ignore' 不影响新字段解析。"""
    cfg = LlmServiceConfig.model_validate({
        "name": "test", "base_url": "http://x", "api_key": "k", "type": "openai-compatible",
        "context_window_tokens": 64000,
        "unknown_field": "should be ignored",
    })
    assert cfg.context_window_tokens == 64000


# ─── OpenAIResponse usage 字段 ───────────────────────────

def test_openai_response_with_usage():
    resp = OpenAIResponse(
        id="chatcmpl-1", object="chat.completion", created=0, model="gpt-4o",
        choices=[
            OpenAIChoice(
                index=0,
                message=OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "hello"),
                finish_reason="stop",
            )
        ],
        usage=OpenAIUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )
    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 50
    assert resp.usage.total_tokens == 150


def test_openai_response_without_usage():
    resp = OpenAIResponse(
        id="chatcmpl-2", object="chat.completion", created=0, model="gpt-4o",
        choices=[
            OpenAIChoice(
                index=0,
                message=OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "hello"),
                finish_reason="stop",
            )
        ],
    )
    assert resp.usage is None


def test_openai_response_usage_from_dict():
    """模拟 litellm ModelResponse.model_dump 输出的 usage 格式。"""
    resp = OpenAIResponse.model_validate({
        "id": "chatcmpl-3", "object": "chat.completion", "created": 0, "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
    })
    assert resp.usage is not None
    assert resp.usage.total_tokens == 280


# ─── GtAgentHistory usage 字段 ───────────────────────────

def test_gt_agent_history_usage_default_none():
    msg = OpenAIMessage.text(OpenaiApiRole.USER, "hello")
    item = GtAgentHistory.build(msg)
    assert item.usage is None


def test_gt_agent_history_usage_settable():
    msg = OpenAIMessage.text(OpenaiApiRole.USER, "hello")
    item = GtAgentHistory.build(msg)
    item.usage = HistoryUsage(estimated_prompt_tokens=100)
    assert item.usage == HistoryUsage(estimated_prompt_tokens=100)
