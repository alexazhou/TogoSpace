"""compactPolicy — Token 预算与 overflow 识别。"""
from __future__ import annotations

import logging
import math
from typing import Any

import litellm

from util import llmApiUtil
from util.configTypes import LlmServiceConfig

logger = logging.getLogger(__name__)

# 系统内置模型上下文长度默认表（仅用于配置未显式覆盖时的兜底）
DEFAULT_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "glm-4.7": 128000,
    "qwen-plus": 131072,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "claude-3-5-sonnet-20241022": 200000,
    "claude-sonnet-4-20250514": 200000,
    "deepseek-chat": 128000,
}

# 用于识别上下文超长的错误关键词
_OVERFLOW_KEYWORDS = (
    "context_length_exceeded",
    "maximum context length",
    "prompt is too long",
    "input is too long",
    "exceeds the context window",
    "too many tokens",
    "context window",
    "max_tokens",
    "token limit",
)

# ─── 阈值计算 ────────────────────────────────────────────

def calc_hard_limit_tokens(model: str, llm_config: LlmServiceConfig) -> int:
    """计算模型当前请求允许使用的最大 prompt token。"""
    context_window = DEFAULT_MODEL_CONTEXT_WINDOWS.get(model, llm_config.context_window_tokens)
    return context_window - llm_config.reserve_output_tokens


def calc_compact_trigger_tokens(model: str, llm_config: LlmServiceConfig) -> int:
    """计算 compact 触发阈值（token 数）。"""
    return math.floor(calc_hard_limit_tokens(model, llm_config) * llm_config.compact_trigger_ratio)


# ─── token 估算 ──────────────────────────────────────────

def estimate_tokens(
    model: str,
    messages: list[llmApiUtil.OpenAIMessage],
    system_prompt: str | None = None,
) -> int:
    """估算消息列表的 token 数量，使用 litellm.token_counter。"""
    try:
        msg_dicts: list[dict[str, Any]] = []
        if system_prompt:
            msg_dicts.append({"role": "system", "content": system_prompt})
        for msg in messages:
            msg_dicts.append(msg.to_dict())
        return litellm.token_counter(model=model, messages=msg_dicts)
    except Exception as e:
        logger.warning("token 估算失败，回退到字符估算: error=%s", e)
        return estimate_token_by_char(messages, system_prompt)


def estimate_token_by_char(messages: list[llmApiUtil.OpenAIMessage], system_prompt: str | None = None) -> int:
    """字符数 / 4 的粗略估算，作为 litellm 失败时的兜底。"""
    total_chars = len(system_prompt or "")
    for msg in messages:
        total_chars += len(msg.content or "")
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total_chars += len(tc.function_args)
    return total_chars // 4


def is_context_overflow_error(error: Exception) -> bool:
    """判断异常是否属于"上下文超长"错误。"""
    error_text = str(error).lower()
    return any(kw in error_text for kw in _OVERFLOW_KEYWORDS)
