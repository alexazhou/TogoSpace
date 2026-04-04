from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from util import llmApiUtil


@dataclass
class GtCoreChatMessage:
    """聊天消息数据类"""
    sender_id: int  # 发送者 agent_id（SpecialAgent 使用固定负值 ID）
    content: str
    send_time: datetime


@dataclass
class GtCoreAgentDialogContext:
    """Agent 发起一次 LLM 请求所需的完整上下文：system prompt + 对话历史 + 模型参数"""
    system_prompt: str
    messages: List[llmApiUtil.OpenAIMessage]
    tools: Optional[list[llmApiUtil.OpenAITool]] = field(default=None)
