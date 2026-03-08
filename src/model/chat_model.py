from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChatMessage:
    """聊天消息数据类"""
    sender_name: str
    content: str
    send_time: datetime
