from dataclasses import dataclass


@dataclass
class ChatMessage:
    """聊天消息数据类"""
    sender: str
    content: str
    timestamp: str
