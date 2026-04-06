"""compactPolicy — Token 预算决策与估算（纯函数模块）。

职责：
- token 估算
- compact 触发阈值计算
- pre-check / post-check / overflow 判断
- compact prompt 模板构造
- usage_json payload 构造

不直接操作 HistoryStore 或发起 LLM 请求。
"""
from __future__ import annotations

import json
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

_COMPACT_PROMPT_TEMPLATE = """\
因为上下文长度即将超出限制，请总结以上的工作内容，作为后续工作的起点。

要求：
- 保留对当前任务仍然有用的事实、约束、决定、未完成事项
- 保留与工具调用结果相关的关键信息
- 删除寒暄、重复表达和已失效上下文
- 输出要简洁、结构化，便于后续继续推理
- 摘要长度尽量简短，不超过 {max_tokens} tokens"""


# ─── 阈值计算 ────────────────────────────────────────────

def resolve_context_window(model: str, llm_config: LlmServiceConfig) -> int:
    """解析模型的上下文窗口大小。优先级：配置 > 内置默认表 > 兜底值。"""
    return DEFAULT_MODEL_CONTEXT_WINDOWS.get(model, llm_config.context_window_tokens)


def calc_compact_trigger_tokens(model: str, llm_config: LlmServiceConfig) -> int:
    """计算 compact 触发阈值（token 数）。"""
    context_window = resolve_context_window(model, llm_config)
    hard_limit = context_window - llm_config.reserve_output_tokens
    return math.floor(hard_limit * llm_config.compact_trigger_ratio)


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
        return _fallback_char_estimate(messages, system_prompt)


def _fallback_char_estimate(
    messages: list[llmApiUtil.OpenAIMessage],
    system_prompt: str | None = None,
) -> int:
    """字符数 / 4 的粗略估算，作为 litellm 失败时的兜底。"""
    total_chars = len(system_prompt or "")
    for msg in messages:
        total_chars += len(msg.content or "")
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total_chars += len(tc.function_args)
    return total_chars // 4


# ─── 决策判断 ────────────────────────────────────────────

def should_trigger_pre_check(estimated_tokens: int, trigger_tokens: int) -> bool:
    """请求前估算是否超阈值。"""
    return estimated_tokens >= trigger_tokens


def should_trigger_post_check(
    actual_prompt_tokens: int,
    trigger_tokens: int,
    has_tool_calls: bool,
) -> bool:
    """请求后实测是否接近上限，且 assistant 返回了 tool_calls。"""
    return actual_prompt_tokens >= trigger_tokens and has_tool_calls


def is_context_overflow_error(error: Exception) -> bool:
    """判断异常是否属于"上下文超长"错误。"""
    error_text = str(error).lower()
    return any(kw in error_text for kw in _OVERFLOW_KEYWORDS)


def should_fail_after_compact(estimated_tokens: int, trigger_tokens: int) -> bool:
    """compact 后再次估算是否仍超限（意味着 compact 未能有效缩减）。"""
    return estimated_tokens >= trigger_tokens


# ─── compact 指令构造 ─────────────────────────────────

def build_compact_instruction(max_tokens: int) -> str:
    """构造追加到消息末尾的 compact 指令。

    不再拼接历史文本，而是作为一条 user 消息追加到原始消息列表末尾，
    让 LLM 在已有上下文中直接总结（有利于 KV 缓存复用）。
    """
    return _COMPACT_PROMPT_TEMPLATE.format(max_tokens=max_tokens)


# ─── usage payload 构造 ──────────────────────────────────

def build_usage_payload(
    *,
    estimated_prompt_tokens: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    pre_check_triggered: bool = False,
    post_check_triggered: bool = False,
    overflow_retry: bool = False,
) -> str:
    """构造 usage_json 字符串，用于写入 agent_histories。"""
    payload = {
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "pre_check_triggered": pre_check_triggered,
        "post_check_triggered": post_check_triggered,
        "overflow_retry": overflow_retry,
    }
    return json.dumps(payload, ensure_ascii=False)
