from dataclasses import dataclass
from typing import List
from datetime import datetime


@dataclass
class Message:
    """消息数据类"""
    sender: str
    content: str
    timestamp: str


class ChatRoom:
    """聊天室类"""

    def __init__(self, name: str, initial_topic: str = ""):
        self.name = name
        self.messages: List[Message] = []
        self.initial_topic = initial_topic

    def add_message(self, sender: str, content: str) -> None:
        """添加消息"""
        message = Message(
            sender=sender,
            content=content,
            timestamp=datetime.now().isoformat()
        )
        self.messages.append(message)

    def get_context(self, max_messages: int = 10) -> str:
        """获取最近的对话上下文"""
        recent_messages = self.messages[-max_messages:]
        context_parts = []
        for msg in recent_messages:
            context_parts.append(f"{msg.sender}: {msg.content}")
        return "\n".join(context_parts)

    def get_context_messages(self, max_messages: int = 10) -> List[dict]:
        """获取结构化的对话上下文消息列表"""
        recent_messages = self.messages[-max_messages:]
        messages = []
        for msg in recent_messages:
            if msg.sender == "system":
                messages.append({"role": "system", "content": msg.content})
            else:
                messages.append({"role": "user", "content": f"{msg.sender}: {msg.content}"})
        return messages

    def format_log(self) -> str:
        """格式化聊天记录"""
        lines = [f"=== {self.name} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.timestamp}] {msg.sender}: {msg.content}")
        return "\n".join(lines)
