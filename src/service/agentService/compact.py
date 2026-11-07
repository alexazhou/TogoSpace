"""compact — Token 预算、overflow 识别与 compact 执行。"""
from __future__ import annotations

import logging
import math
from typing import Any

import litellm

from constants import OpenaiApiRole
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from model.dbModel.agentMessage import AgentMessage
from service import llmService
from service.agentService import promptBuilder
from util import llmApiUtil, configUtil
from util.configUtil.configTypes import LlmContextConfig, LlmModelConfig

logger = logging.getLogger(__name__)

# 系统内置模型上下文长度表：仅在用户未显式调整 context_window_tokens 时作为兜底。
# 按模型名精确匹配，因此只有名称与下表完全一致的模型才会命中。
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

def resolve_context_window_tokens(model_config: LlmModelConfig) -> int:
    """解析模型当前生效的上下文窗口原始值（未扣 `reserve_output_tokens`）。

    context_window 取值优先级：用户配置（模型级 > 全局）> 内置模型表 > 代码默认值。
    内置表只在用户未调整 context_window_tokens（仍为代码默认值）时生效。

    单独抽出来是因为「model 的窗口是多少」有两个消费方：`calc_hard_limit_tokens`
    要拿它扣 reserve，前端 tooltip 要直接展示它。两处各写一遍就等于把
    DEFAULT_MODEL_CONTEXT_WINDOWS 的兜底规则复制了两份，将来只改一处就分叉。
    """
    setting = configUtil.get_app_config().setting
    context_cfg = model_config.context_config if model_config.context_config else setting.context_config

    # context_cfg 已按「模型级 > 全局 > 默认值」合并过，这里即用户最终生效的配置。
    context_window = context_cfg.context_window_tokens
    if context_window == LlmContextConfig().context_window_tokens:
        # 用户没改过 → 用内置表兜底；表里没有该模型则保持代码默认值。
        context_window = DEFAULT_MODEL_CONTEXT_WINDOWS.get(model_config.name, context_window)

    return context_window


def calc_hard_limit_tokens(model_config: LlmModelConfig) -> int:
    """计算模型当前请求允许使用的最大 prompt token。

    context_window 取值见 `resolve_context_window_tokens`；此处再扣掉为输出预留的
    `reserve_output_tokens`。这个值就是上下文占用率的分母（见 `AgentContextUsage`）。
    """
    setting = configUtil.get_app_config().setting
    context_cfg = model_config.context_config if model_config.context_config else setting.context_config
    return resolve_context_window_tokens(model_config) - context_cfg.reserve_output_tokens


def calc_compact_trigger_tokens(model_config: LlmModelConfig) -> int:
    """计算 compact 触发阈值（token 数）。"""
    setting = configUtil.get_app_config().setting
    context_cfg = model_config.context_config if model_config.context_config else setting.context_config
    return math.floor(calc_hard_limit_tokens(model_config) * context_cfg.compact_trigger_ratio)




# ─── token 估算 ──────────────────────────────────────────

def estimate_tokens(
    model: str,
    messages: list[AgentMessage],
    system_prompt: str | None = None,
) -> int:
    """估算消息列表的 token 数量，使用 litellm.token_counter。

    内部把 AgentMessage 转回 OpenAIMessage（含多模态 content）再估算，
    保证与实际发送形态一致。
    """
    try:
        msg_dicts: list[dict[str, Any]] = []
        if system_prompt:
            msg_dicts.append({"role": "system", "content": system_prompt})
        for msg in messages:
            msg_dicts.append(msg.to_openai_message().to_dict())
        return litellm.token_counter(model=model, messages=msg_dicts)
    except Exception as e:
        logger.warning("token 估算失败，回退到字符估算: error=%s", e)
        return estimate_token_by_char(messages, system_prompt)


def estimate_token_by_char(messages: list[AgentMessage], system_prompt: str | None = None) -> int:
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

class NoCompactableHistoryError(RuntimeError):
    """「当前没有可压缩的历史」这一确定性业务拒绝。

    继承 `RuntimeError` 是**行为兼容**的选择：`_execute_compact` 原本就抛
    `RuntimeError`，既有 `except RuntimeError` 的调用点（预检 / 复检 compact 路径）
    照样接住并继续往外抛，语义不变。

    引入它的唯一目的，是让**接口层**能把「用户点了压缩、但没有可压缩内容」这种
    正常业务拒绝与真正的故障（LLM 失败、摘要为空返回值等）区分开：
    前者回 400 + `no_compactable_history`，后者必须仍然是 500。
    用 `str(exc)` 做子串匹配也能实现，但那等于把异常语义写进错误文案里，
    改一句提示语就静默失效——所以用类型表达。
    """


class CompactInProgressError(RuntimeError):
    """「另一个 compact 正在跑」这一并发拒绝。

    与 `NoCompactableHistoryError` 同族：都是**确定性业务拒绝**（回 400），
    不是故障（500）。接口层用异常类型区分，理由同上——错误文案会改，类型不会。

    为什么需要它而不只靠接口层的预检：预检与真正置位之间如果将来被插入 await，
    两个请求会一起进临界区。置位动作本身是「检查并占用」的原子操作，
    它拒绝时抛这个错，接口层兜住即可——预检只是让最常见的那条路径少一次
    无效的协程进入，不是唯一防线。
    """


async def compact_messages(
    messages: list[AgentMessage],
    system_prompt: str,
    model: str,
    *,
    session_id: str,
    tools: list[llmApiUtil.OpenAITool] | None = None,
    max_tokens: int = 26214,  # 默认约为 262144 上下文长度的 10%，建议由调用方按实际 context_window_tokens 动态计算
) -> str:
    """压缩消息列表，返回已包含引导语的摘要文本，失败时抛出异常。

    Args:
        messages: 待压缩的消息列表
        system_prompt: 系统提示（用于正确理解上下文）
        model: 模型名称
        session_id: 被压缩的旧 session id（摘要请求算在旧 session 上）
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
        messages=messages + [AgentMessage(role=OpenaiApiRole.USER, content=instruction)],
        tools=tools,
        tool_choice="none",
        session_id=session_id,
    )
    infer_result = await llmService.infer(model, ctx)

    if infer_result.ok is False:
        raise RuntimeError(infer_result.error_message or "LLM inference failed during compact")

    if infer_result.response is None:
        raise RuntimeError("LLM returned empty response during compact")
    response_message = infer_result.response.choices[0].message

    if response_message.tool_calls:
        raise RuntimeError("Model returned tool_calls instead of summary during compact")
    summary = response_message.text_content() or ""
    return promptBuilder.build_compact_resume_prompt(summary)
