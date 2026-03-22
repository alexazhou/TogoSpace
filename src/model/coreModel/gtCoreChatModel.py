from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from util import llmApiUtil


@dataclass
class ChatMessage:
    """聊天消息数据类"""
    sender_name: str
    content: str
    send_time: datetime


@dataclass
class AgentDialogContext:
    """Agent 发起一次 LLM 请求所需的完整上下文：system prompt + 对话历史 + 模型参数"""
    system_prompt: str
    messages: List[llmApiUtil.LlmApiMessage]
    tools: Optional[list[llmApiUtil.Tool]] = field(default=None)
