from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from util import llmApiUtil


@dataclass
class GtCoreRoomMessage:
    """房间消息数据类"""
    sender_id: int  # 发送者 agent_id（SpecialAgent 使用固定负值 ID）
    sender_name: str  # 发送者稳定标识名
    content: str
    send_time: datetime
    sender_i18n: dict = field(default_factory=dict)  # 发送者多语言信息，含 display_name


@dataclass
class GtCoreAgentDialogContext:
    """Agent 发起一次 LLM 请求所需的完整上下文：system prompt + 对话历史 + 模型参数"""
    system_prompt: str
    messages: List[llmApiUtil.OpenAIMessage]
    tools: Optional[list[llmApiUtil.OpenAITool]] = field(default=None)
    tool_choice: Optional[str | dict[str, Any]] = field(default=None)
    prompt_cache: bool = field(default=True)
