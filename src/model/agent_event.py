from dataclasses import dataclass
from enum import Enum


class RoomState(Enum):
    SCHEDULING = "scheduling"  # 房间正在调度，有事件待处理
    IDLE = "idle"              # 房间空闲，无更多事件


@dataclass
class RoomMessageEvent:
    """Agent 收到聊天室新消息的事件。"""
    room_name: str
