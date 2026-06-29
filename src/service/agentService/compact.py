"""compact — Token 预算、overflow 识别与 compact 执行。"""
from __future__ import annotations

import logging
import math
from typing import Any

import litellm

from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service import llmService
from service.agentService import promptBuilder
from util import llmApiUtil, configUtil
from util.configTypes import LlmModelConfig

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
    "input too long",
    "exceeds the context window",
    "too many tokens",
    "context window",
    "max_tokens",
    "token limit",
)

# ─── 阈值计算 ────────────────────────────────────────────

def calc_hard_limit_tokens(model: str, model_config: LlmModelConfig) -> int:
    """计算模型当前请求允许使用的最大 prompt token。"""
    setting = configUtil.get_app_config().setting
    context_cfg = model_config.context_config if model_config.context_config else setting.context_config
    context_window = DEFAULT_MODEL_CONTEXT_WINDOWS.get(model, context_cfg.context_window_tokens)
    return context_window - context_cfg.reserve_output_tokens


def calc_compact_trigger_tokens(model: str, model_config: LlmModelConfig) -> int:
    """计算 compact 触发阈值（token 数）。"""
    setting = configUtil.get_app_config().setting
    context_cfg = model_config.context_config if model_config.context_config else setting.context_config
    return math.floor(calc_hard_limit_tokens(model, model_config) * context_cfg.compact_trigger_ratio)


def resolve_compact_config(model_name: str | None) -> tuple[str, LlmModelConfig, int, int]:
    """获取 compact 相关配置：(resolved_model, model_config, trigger_tokens, hard_limit_tokens)。

    Args:
        model_name: 代理模型标识，支持 primary/lightweight/vision 别名或 model@provider 格式。

    Returns:
        (resolved_model, model_config, trigger_tokens, hard_limit_tokens)
    """
    from service.llmService.core import resolve_model
    try:
        provider_config, model_config = resolve_model(model_name)
    except ValueError as e:
        raise ValueError(f"无法解析代理所使用的模型配置: {e}")

    resolved_model = f"{model_config.name}@{provider_config.name}"
    trigger_tokens = calc_compact_trigger_tokens(resolved_model, model_config)
    hard_limit_tokens = calc_hard_limit_tokens(resolved_model, model_config)
    return resolved_model, model_config, trigger_tokens, hard_limit_tokens


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


# ─── compact 执行 ─────────────────────────────────────────

async def compact_messages(
    messages: list[llmApiUtil.OpenAIMessage],
    system_prompt: str,
    model: str,
    tools: list[llmApiUtil.OpenAITool] | None = None,
    max_tokens: int = 13107,  # 默认约为 131072 上下文长度的 10%，建议由调用方按实际 context_window_tokens 动态计算
) -> str:
    """压缩消息列表，返回已包含引导语的摘要文本，失败时抛出异常。

    Args:
        messages: 待压缩的消息列表
        system_prompt: 系统提示（用于正确理解上下文）
        model: 模型名称
        tools: 透传当前工具列表，以保持请求形态稳定
        max_tokens: 摘要最大 token 数，建议为 context_window_tokens 的 10%

    Returns:
        摘要文本（已包含引导语）

    Raises:
        RuntimeError: LLM 推理失败、返回为空、或返回了 tool_calls
    """
    instruction = promptBuilder.build_compact_instruction(max_tokens)
    ctx = GtCoreAgentDialogContext(
        system_prompt=system_prompt,
        messages=messages + [llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, instruction)],
        tools=tools,
        tool_choice="none",
    )
    infer_result = await llmService.infer(model, ctx)

    if infer_result.ok is False:
        raise RuntimeError(infer_result.error_message or "LLM inference failed during compact")

    if infer_result.response is None:
        raise RuntimeError("LLM returned empty response during compact")
    response_message = infer_result.response.choices[0].message

    if response_message.tool_calls:
        raise RuntimeError("Model returned tool_calls instead of summary during compact")
    summary = response_message.content or ""
    return promptBuilder.build_compact_resume_prompt(summary)
