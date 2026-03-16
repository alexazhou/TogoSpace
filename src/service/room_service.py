from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from service import message_bus
from model.chat_model import ChatMessage
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent

logger = logging.getLogger(__name__)


def _make_room_key(team_name: str, room_name: str) -> str:
    return f"{room_name}@{team_name}"


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    def __init__(self, name: str, team_name: str, initial_topic: str = "", room_type: RoomType = RoomType.GROUP):
        self.name: str = name  # 房间名称
        self.team_name: str = team_name  # 所属 Team
        self.room_type: RoomType = room_type  # 房间类型（私有/群聊）
        self.messages: List[ChatMessage] = []  # 消息历史记录
        self.initial_topic: str = initial_topic  # 初始话题
        self.agents: List[str] = []  # 房间参与者名单（包含 Operator 和 AI Agents）

        self._agent_read_index: Dict[str, int] = {}  # 每个 Agent 的消息读取进度
        self._turn_index: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._max_turns: int = 0  # 最大允许轮次
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.IDLE  # 房间当前的调度状态
        self._round_skipped: set = set()  # 当前轮次已跳过发言的成员名单

    @property
    def key(self) -> str:
        return _make_room_key(self.team_name, self.name)

    @property
    def state(self) -> RoomState:
        return self._state

    def setup_turns(self, agent_names: List[str], max_turns: int) -> None:
        """初始化轮次控制，并向第一位参与者发布初始事件。"""
        self._turn_index = 0
        self._max_turns = max_turns
        self._turn_pos = 0
        self._round_skipped = set()
        if self.agents and max_turns > 0:
            self._state = RoomState.SCHEDULING
            self._publish_current_turn()

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
            room_key=self.key,
            team_name=self.team_name,
            sender=sender,
            content=content,
            time=message.send_time.isoformat(),
        )

        if not self.agents:
            return

        # 1. 唤醒检查：如果房间已停止（无论原因），任何新消息都将重置轮次并恢复调度
        was_idle = (self._state == RoomState.IDLE)
        if was_idle:
            logger.info(f"检测到房间 {self.key} 的活动 ({sender})，重置轮次计数器并唤醒房间")
            self._turn_index = 0
            self._round_skipped = set()
            self._state = RoomState.SCHEDULING

        # 2. 推进逻辑：只有当前顺序发言人说话，才推进到下一位
        current_expected = self.get_current_turn_agent()
        if sender == current_expected:
            self._advance_turn()
        else:
            logger.info(f"房间 {self.key} 收到来自 {sender} 的插话，保持当前发言位 (当前应轮到 {current_expected})")
            # 如果刚才从 IDLE 唤醒，且是一次插话，我们需要手动重发当前轮次事件以重启循环
            if was_idle:
                self._publish_current_turn()

    def skip_turn(self, sender: str = None) -> bool:
        """跳过当前发言人的轮次。通常由 Agent 在 skip_chat_msg 工具中调用。
        返回 True 表示跳过成功，False 表示被拒绝（sender 不是当前发言人）。
        """
        current_expected = self.get_current_turn_agent()

        # 如果指定了发送者且不匹配，则拒绝跳过（防止误操作）
        if sender and sender != current_expected:
            logger.warning(f"拒绝跳过申请：{sender} 并非当前发言人 ({current_expected})")
            return False

        logger.info(f"房间 {self.key} 由 {current_expected} 跳过一轮发言")
        self._round_skipped.add(current_expected)
        self._advance_turn()
        return True

    def get_current_turn_agent(self) -> Optional[str]:
        """返回当前理论上应该发言的 Agent 名（忽略 IDLE 状态）。"""
        if not self.agents:
            return None
        return self.agents[self._turn_pos]

    def _publish_current_turn(self) -> None:
        """发布当前轮次的发言事件。"""
        next_name = self.get_current_turn_agent()
        if next_name:
            message_bus.publish(
                MessageBusTopic.ROOM_AGENT_TURN,
                agent_name=next_name,
                room_name=self.name,
                room_key=self.key,
                team_name=self.team_name,
            )

    def _advance_turn(self) -> None:
        """推进轮次位置索引。内部私有方法。"""
        if not self.agents:
            return

        self._turn_pos += 1

        # 本轮所有人发言完毕 → 轮次 +1，重置位置
        if self._turn_pos >= len(self.agents):
            # 全员名单与跳过名单对比（排除 Operator，它不参与 AI 发言判定）
            ai_agents = set(a for a in self.agents if a != SpecialAgent.OPERATOR)
            all_skipped = ai_agents and ai_agents.issubset(self._round_skipped)
            self._round_skipped = set()  # 下一轮重新收集
            self._turn_index += 1
            self._turn_pos = 0

            # 全员跳过 → 立即停止调度
            if all_skipped:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 本轮所有成员均跳过发言，停止调度")
                return

            # 正常达到最大轮次
            if self._turn_index >= self._max_turns:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
                return

        # 正常发布下一位成员的发言事件
        self._publish_current_turn()

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
        lines = [f"=== {self.key} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.send_time.isoformat()}] {msg.sender_name}: {msg.content}")
        return "\n".join(lines)


_rooms: Dict[str, ChatRoom] = {}


def startup() -> None:
    """初始化房间服务，清空所有房间。"""
    _rooms.clear()


def create_room(team_name: str, name: str, members: List[str], initial_topic: str = "", room_type: RoomType = RoomType.GROUP, max_turns: int = 0) -> None:
    """创建并初始化一个聊天室，设置成员并发布系统公告。若 max_turns > 0 则初始化轮次。"""
    room = ChatRoom(name=name, team_name=team_name, initial_topic=initial_topic, room_type=room_type)
    room.agents = members
    room_key = room.key
    _rooms[room_key] = room

    logger.info(f"创建并初始化聊天室: key={room_key}, type={room_type.value}, 成员={members}")

    member_list_str = "、".join(members)
    msg = f"{name} 房间已经创建，当前房间成员：{member_list_str}"
    if initial_topic:
        msg += f"\n本房间初始话题：{initial_topic}"
    room.add_message("system", msg)

    if max_turns > 0:
        logger.info(f"初始化轮次配置: room={room_key}, max_turns={max_turns}")
        room.setup_turns(members, max_turns)


def create_rooms(teams_config: list) -> None:
    """遍历所有 team，根据 groups 配置批量创建聊天室。"""
    for team in teams_config:
        team_name = team["name"]
        for group in team["groups"]:
            create_room(
                team_name=team_name,
                name=group["name"],
                members=group["members"],
                initial_topic=group.get("initial_topic", ""),
                room_type=RoomType(group.get("type", "group")),
                max_turns=group.get("max_turns", 0),
            )


def get_member_names(team_name: str, room_name: str) -> List[str]:
    """返回聊天室的参与者名列表。"""
    return _rooms[_make_room_key(team_name, room_name)].agents


def get_rooms_for_agent(team_name: str, agent_name: str) -> List[str]:
    """返回指定参与者所在的房间 key 列表。可选按 team 过滤。"""
    results = []
    for key, room in _rooms.items():
        if agent_name in room.agents:
            if team_name is None or room.team_name == team_name:
                results.append(key)
    return results


def get_room(room_key: str) -> ChatRoom:
    """返回指定聊天室实例（使用 room@team 格式的 key）。"""
    room = _rooms.get(room_key)
    if room is None:
        raise RuntimeError(f"聊天室 '{room_key}' 不存在")
    return room


def get_all_rooms() -> List[ChatRoom]:
    """返回所有聊天室实例列表。"""
    return list(_rooms.values())


def shutdown() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()
