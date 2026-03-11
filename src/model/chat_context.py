from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from service.room_service import ChatRoom


@dataclass
class ChatContext:
    """工具调用时注入的上下文，包含当前 Agent 和聊天室信息。"""
    agent_name: str
    team_name: str
    chat_room: ChatRoom
    get_room: Callable[[str], ChatRoom]
