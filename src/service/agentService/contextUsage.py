"""contextUsage — Agent 上下文占用率（分子 / 分母 / 触发线）的统一计算与广播。

这个模块存在的意义是**只留一份口径**。占用率牵扯三方：
- 后端自动 compact（`agentTurnRunner._check_compact`）用 `calc_compact_trigger_tokens` 判是否该压；
- 接口 `GET /agents/{id}.json` 的首屏快照；
- WebSocket 增量推送。

如果三处各自算一遍 `used / total`，迟早出现「面板显示 85% 但没触发压缩」这种
两边都对、口径不同的鬼故事。所以统一走这里，并且分母与触发线直接复用
`compact.py` 的函数，不另写估算。
"""
from __future__ import annotations

import logging

from constants import MessageBusTopic
from model.coreModel.agentModel import AgentContextUsage
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.agentMessage import AgentMessage
from service import messageBus
from service.agentService import compact
from service.llmService.core import resolve_model

logger = logging.getLogger(__name__)


def calc_context_usage(
    *,
    model_name: str,
    messages: list[AgentMessage],
    system_prompt: str | None,
    used_tokens: int | None = None,
) -> AgentContextUsage | None:
    """按当前模型配置算一份占用快照；模型配置解析失败时返回 `None`。

    Args:
        model_name: Agent 的模型标识（可为空串，走系统默认槽位）。
        messages: 未压缩的历史消息（**不含** System Prompt）。
        system_prompt: System Prompt 正文；`estimate_tokens` 会把它的开销算进去。
        used_tokens: 已知的精确用量（如推理返回的 `usage.prompt_tokens`）。
            给了就直接用，跳过 token 估算——估算函数每次都要跑一遍 tokenizer，
            在推理收尾这个已知精确值的场景里没必要重复算。

    Returns:
        `AgentContextUsage`；模型配置不可解析时 `None`（前端整条不渲染）。
    """
    try:
        _, merged_llm_config = resolve_model(model_name)
    except ValueError as e:
        # 配置缺失 / provider 被禁用：这不是「用量为 0」，是「算不出来」。
        logger.warning("[context-usage] 模型配置解析失败，用量置 None: error=%s", e)
        return None

    limit_tokens = compact.calc_hard_limit_tokens(merged_llm_config)
    if limit_tokens <= 0:
        # reserve_output_tokens 配得比 context_window 还大时的病态配置。
        # 分母非正，比率无意义——返回 None 而不是造一个除零或负数比例。
        logger.warning(
            "[context-usage] limit_tokens 非正，用量置 None: limit=%d, model=%s",
            limit_tokens, merged_llm_config.name,
        )
        return None

    if used_tokens is None:
        used_tokens = compact.estimate_tokens(merged_llm_config.name, messages, system_prompt)

    return AgentContextUsage(
        used_tokens=used_tokens,
        limit_tokens=limit_tokens,
        compact_trigger_tokens=compact.calc_compact_trigger_tokens(merged_llm_config),
        context_window_tokens=compact.resolve_context_window_tokens(merged_llm_config),
        # 不 clamp：超限是真实可发生的诊断信息，压掉就看不见了（见 AgentContextUsage 注释）。
        ratio=used_tokens / limit_tokens,
    )


def calc_context_usage_from_history(
    *,
    model_name: str,
    items: list[GtAgentHistory],
    system_prompt: str | None,
) -> AgentContextUsage | None:
    """从历史行列表算占用快照（内存态与 DB 态共用同一份转换规则）。

    只取 `message` 非空的条目：pending infer 占位行、被中断的空行都没有消息体，
    把它们算进去会让分子虚高。
    """
    messages = [msg for item in items if (msg := item.message) is not None]
    return calc_context_usage(
        model_name=model_name,
        messages=messages,
        system_prompt=system_prompt,
    )


def publish_context_usage(
    *,
    team_id: int,
    agent_id: int,
    usage: AgentContextUsage | None,
) -> None:
    """广播用量变化。

    只在**拿到新数值之后**调用（推理收尾 / compact 完成 / 轮次装载）：
    没有计算就没有广播，避免前端收到一堆 `null` 把已有数值抹掉。

    负 id（Operator / SYSTEM）直接跳过：它们不消耗 LLM 上下文，
    广播出去前端也不渲染，白白多一帧。
    """
    if agent_id <= 0 or team_id <= 0 or usage is None:
        return

    messageBus.publish(
        MessageBusTopic.AGENT_CONTEXT_USAGE_CHANGED,
        team_id=team_id,
        agent_id=agent_id,
        context_usage=usage.model_dump(mode="json"),
    )
