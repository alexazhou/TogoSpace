from typing import Dict, List
from datetime import datetime

from model.chat_model import ChatMessage


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    def __init__(self, name: str, initial_topic: str = ""):
        self.name = name
        self.messages: List[ChatMessage] = []
        self.initial_topic = initial_topic

    def add_message(self, sender: str, content: str) -> None:
        message = ChatMessage(
            sender=sender,
            content=content,
            timestamp=datetime.now().isoformat()
        )
        self.messages.append(message)

    def get_context(self, max_messages: int = 10) -> str:
        recent = self.messages[-max_messages:]
        return "\n".join(f"{m.sender}: {m.content}" for m in recent)

    def get_context_messages(self, max_messages: int = 10) -> List[dict]:
        recent = self.messages[-max_messages:]
        result = []
        for msg in recent:
            if msg.sender == "system":
                result.append({"role": "system", "content": msg.content})
            else:
                result.append({"role": "user", "content": f"{msg.sender}: {msg.content}"})
        return result

    def format_log(self) -> str:
        lines = [f"=== {self.name} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.timestamp}] {msg.sender}: {msg.content}")
        return "\n".join(lines)


_rooms: Dict[str, ChatRoom] = {}


def init(name: str, initial_topic: str = "") -> None:
    """创建并注册一个聊天室。"""
    _rooms[name] = ChatRoom(name=name, initial_topic=initial_topic)


def close(name: str) -> None:
    """移除指定聊天室。"""
    _rooms.pop(name, None)


def close_all() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()


def get_room(name: str) -> ChatRoom:
    """返回指定聊天室实例（供需要传递对象的场景使用，如 agent_context）。"""
    room = _rooms.get(name)
    if room is None:
        raise RuntimeError(f"聊天室 '{name}' 不存在，请先调用 init(name)")
    return room


