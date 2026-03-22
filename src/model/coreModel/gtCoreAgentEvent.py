from dataclasses import dataclass


@dataclass
class RoomMessageEvent:
    """Agent 收到聊天室新消息的事件。"""
    room_id: str  # room@team 格式
