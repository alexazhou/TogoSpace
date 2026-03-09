from __future__ import annotations

from typing import Dict, List
from datetime import datetime

from model.chat_model import ChatMessage
from model.agent_event import RoomMessageEvent


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    def __init__(self, name: str, initial_topic: str = ""):
        self.name = name
        self.messages: List[ChatMessage] = []
        self.initial_topic = initial_topic
        self._agent_read_index: Dict[str, int] = {}
        self._turn_agents: list = []
        self._turn_index: int = 0
        self._max_turns: int = 0
        self._turn_pos: int = 0

    def setup_turns(self, agents: list, max_turns: int) -> None:
        """初始化轮次控制，并向第一位参与者推送初始事件。"""
        self._turn_agents = agents
        self._turn_index = 0
        self._max_turns = max_turns
        self._turn_pos = 0
        if agents and max_turns > 0:
            agents[0].wait_event_queue.put_nowait(RoomMessageEvent(self.name))

    def get_unread_messages(self, agent_name: str) -> List[ChatMessage]:
        """返回 agent_name 尚未读取的新消息，并推进其读取位置。"""
        read_idx: int = self._agent_read_index.get(agent_name, 0)
        new_msgs: List[ChatMessage] = self.messages[read_idx:]
        self._agent_read_index[agent_name] = len(self.messages)
        return new_msgs

    def add_message(self, sender: str, content: str) -> None:
        message = ChatMessage(
            sender_name=sender,
            content=content,
            send_time=datetime.now()
        )
        self.messages.append(message)

        if not self._turn_agents:
            return

        self._turn_pos += 1

        # 本轮所有人发言完毕 → 轮次 +1，重置位置
        if self._turn_pos >= len(self._turn_agents):
            self._turn_index += 1
            self._turn_pos = 0

        # 达到最大轮次 → 不再推送
        if self._turn_index >= self._max_turns:
            return

        next_agent = self._turn_agents[self._turn_pos]
        next_agent.wait_event_queue.put_nowait(RoomMessageEvent(self.name))

    def get_context(self, max_messages: int = 10) -> str:
        recent = self.messages[-max_messages:]
        return "\n".join(f"{m.sender}: {m.content}" for m in recent)

    def get_context_messages(self, max_messages: int = 10) -> List[dict]:
        recent = self.messages[-max_messages:]
        result = []
        for msg in recent:
            if msg.sender_name == "system":
                result.append({"role": "system", "content": msg.content})
            else:
                result.append({"role": "user", "content": f"{msg.sender_name}: {msg.content}"})
        return result

    def format_log(self) -> str:
        lines = [f"=== {self.name} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.send_time.isoformat()}] {msg.sender_name}: {msg.content}")
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


