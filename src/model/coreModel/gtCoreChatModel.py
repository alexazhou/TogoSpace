from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from util import llmApiUtil


@dataclass
class GtCoreRoomMessage:
    """房间消息数据类"""
    sender_id: int  # 发送者 agent_id（SpecialAgent 使用固定负值 ID）
    sender_display_name: str  # 发送者显示名称（创建时根据语言固定）
    content: str
    send_time: datetime
    insert_immediately: bool = False  # V20: 运行中即时插入标志，持久化至 DB
    # V20: 消息在房间内的显示顺序（由 RoomMessageStore 在进入主消息列表时赋值）。
    # immediately 消息在注入前为 None，注入时由 agentTurnRunner 赋值，此后持久化至 DB。
    seq: int | None = None
    db_id: int | None = None  # 对应数据库记录 ID，用于 immediately 消息注入时更新 seq


@dataclass
class GtCoreAgentDialogContext:
    """Agent 发起一次 LLM 请求所需的完整上下文：system prompt + 对话历史 + 模型参数"""
    system_prompt: str
    messages: List[llmApiUtil.OpenAIMessage]
    tools: Optional[list[llmApiUtil.OpenAITool]] = field(default=None)
    tool_choice: Optional[str | dict[str, Any]] = field(default=None)
    prompt_cache: bool = field(default=True)
