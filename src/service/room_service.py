from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List

from service import message_bus
from model.chat_model import ChatMessage
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent

logger = logging.getLogger(__name__)


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    def __init__(self, name: str, initial_topic: str = "", room_type: RoomType = RoomType.GROUP):
        self.name = name
        self.room_type = room_type
        self.messages: List[ChatMessage] = []
        self.initial_topic = initial_topic
        self.member_names: List[str] = []
        self._agent_read_index: Dict[str, int] = {}
        self._turn_agents: List[str] = []
        self._turn_index: int = 0
        self._max_turns: int = 0
        self._turn_pos: int = 0
        self._state: RoomState = RoomState.IDLE

    @property
    def state(self) -> RoomState:
        return self._state

    def setup_turns(self, agent_names: List[str], max_turns: int) -> None:
        """初始化轮次控制，并向第一位参与者发布初始事件。"""
        self._turn_agents = agent_names
        self._turn_index = 0
        self._max_turns = max_turns
        self._turn_pos = 0
        if agent_names and max_turns > 0:
            self._state = RoomState.SCHEDULING
            message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name=agent_names[0], room_name=self.name)

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
        message_bus.publish(
            MessageBusTopic.ROOM_MSG_ADDED,
            room_name=self.name,
            sender=sender,
            content=content,
            time=message.send_time.isoformat(),
        )
        self._advance_turn()

    def skip_turn(self) -> None:
        """跳过当前发言人的轮次，直接推进到下一位。"""
        logger.info(f"房间 {self.name} 跳过一轮发言")
        self._advance_turn()

    def _advance_turn(self) -> None:
        """推进轮次索引并发布事件。内部私有方法。"""
        if not self._turn_agents:
            return

        # 如果当前已达到最大轮次（处于 IDLE 状态），但依然触发了推进（说明有 Agent 主动发言或人类干预）
        # 则重置轮次计数器，重新进入调度状态
        if self._state == RoomState.IDLE and self._turn_index >= self._max_turns:
            logger.info(f"房间 {self.name} 达到最大轮次后仍有活动，重置计数器并重新开始对话循环")
            self._turn_index = 0
            self._state = RoomState.SCHEDULING

        self._turn_pos += 1

        # 本轮所有人发言完毕 → 轮次 +1，重置位置
        if self._turn_pos >= len(self._turn_agents):
            self._turn_index += 1
            self._turn_pos = 0

        # 达到最大轮次 → 房间进入空闲，不再推送
        if self._turn_index >= self._max_turns:
            self._state = RoomState.IDLE
            logger.info(f"房间 {self.name} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
            return

        next_name = self._turn_agents[self._turn_pos]
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name=next_name, room_name=self.name)

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


def init(name: str, initial_topic: str = "", room_type: RoomType = RoomType.GROUP) -> None:
    """创建并注册一个聊天室。"""
    _rooms[name] = ChatRoom(name=name, initial_topic=initial_topic, room_type=room_type)
    logger.info(f"创建聊天室: name={name}, type={room_type.value}, initial_topic={initial_topic!r}")


def close(name: str) -> None:
    """移除指定聊天室。"""
    _rooms.pop(name, None)


def close_all() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()


def setup_members(room_name: str, agent_names: List[str]) -> None:
    """设置聊天室的 Agent 成员列表。"""
    _rooms[room_name].member_names = agent_names
    logger.info(f"注册成员: name={room_name}, 成员={agent_names}")


def get_member_names(room_name: str) -> List[str]:
    """返回聊天室的 Agent 成员名列表。"""
    return _rooms[room_name].member_names


def get_rooms_for_agent(agent_name: str) -> List[str]:
    """返回指定 Agent 所在的所有房间名列表。"""
    return [name for name, room in _rooms.items() if agent_name in room.member_names]


def get_room(name: str) -> ChatRoom:
    """返回指定聊天室实例（供需要传递对象的场景使用，如 agent_context）。"""
    room = _rooms.get(name)
    if room is None:
        raise RuntimeError(f"聊天室 '{name}' 不存在，请先调用 init(name)")
    return room


def get_all_rooms() -> List[ChatRoom]:
    """返回所有聊天室实例列表。"""
    return list(_rooms.values())


