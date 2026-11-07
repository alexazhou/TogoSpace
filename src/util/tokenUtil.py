"""tokenUtil — 本地 token 估算工具。

流式展示需要「每个 delta 都能算一次」的估算能力，必须纯本地、低开销：
litellm 自带的 tiktoken 编码文件（cl100k / o200k / anthropic）已随桌面安装包
发布，`litellm.token_counter` 不会发起网络请求。

这里只负责「一段文本大概多少 token」。消息列表级估算仍走
`agentService.compact.estimate_tokens`（它会处理 AgentMessage 转换）。
"""
from __future__ import annotations

import logging

import litellm

logger = logging.getLogger(__name__)


def estimate_text_tokens(model: str, text: str | None) -> int:
    """估算一段文本的 token 数，失败时回退 `字符数 / 4`。

    调用方按流式增量逐次调用（每次只传新到的 delta），总成本与输出长度成正比。
    """
    if not text:
        return 0
    try:
        return litellm.token_counter(model=model, text=text)
    except Exception as e:
        logger.debug("token 估算失败，回退字符估算: error=%s", e)
        return max(1, len(text) // 4)
