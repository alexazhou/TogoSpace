from dataclasses import dataclass


@dataclass
class GtCoreRoomMessageEvent:
    """Agent 收到聊天室新消息的事件。"""
    room_id: int  # 数据库 ID
