from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional

from model.dbModel.agentMessage import AgentMessage


@dataclass
class GtCoreAgentDialogContext:
    """Agent 发起一次 LLM 请求所需的完整上下文：system prompt + 对话历史 + 模型参数

    `messages` 为 AgentMessage（存储域类型），发送前由 llmService 统一转 OpenAIMessage。
    """
    system_prompt: str
    messages: List[AgentMessage]
    tools: Optional[list] = field(default=None)
    tool_choice: Optional[str | dict[str, Any]] = field(default=None)
    prompt_cache: bool = field(default=True)
