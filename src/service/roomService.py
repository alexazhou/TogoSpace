from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from service import messageBus, persistenceService
from model.coreModel.gtCoreChatModel import ChatMessage
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    """工具调用时注入的上下文，包含当前 Agent 和聊天室信息。"""
    agent_name: str
    team_name: str
    chat_room: ChatRoom


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    def __init__(self, name: str, team_name: str, initial_topic: str = "", room_type: RoomType = RoomType.GROUP,
                 members: List[str] = None, max_turns: int = 0):
        self.name: str = name  # 房间名称
        self.team_name: str = team_name  # 所属 Team
        self.room_type: RoomType = room_type  # 房间类型（私有/群聊）
        self.messages: List[ChatMessage] = []  # 消息历史记录
        self.initial_topic: str = initial_topic  # 初始话题
        self.agents: List[str] = members or []  # 房间参与者名单（包含 Operator 和 AI Agents）

        self._agent_read_index: Dict[str, int] = {}  # 每个 Agent 的消息读取进度
        self._turn_index: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._max_turns: int = max_turns  # 最大允许轮次
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.IDLE  # 房间当前的调度状态
        self._round_skipped: set = set()  # 当前轮次已跳过发言的成员名单
        self._current_turn_has_content: bool = False  # 当前发言人是否已发送内容

        if self.agents and max_turns > 0:
            self._state = RoomState.SCHEDULING

    @property
    def key(self) -> str:
        return f"{self.name}@{self.team_name}"

    @property
    def state(self) -> RoomState:
        return self._state

    async def get_unread_messages(self, agent_name: str) -> List[ChatMessage]:
        """返回 agent_name 尚未读取的新消息，并推进其读取位置。"""
        read_idx = self._agent_read_index.get(agent_name, 0)
        new_msgs = self.messages[read_idx:]
        self._agent_read_index[agent_name] = len(self.messages)
        await self._persist_read_index()
        return new_msgs

    async def add_message(self, sender: str, content: str) -> None:
        await self._append_message(sender, content, publish_events=True, persist=True)

    async def _append_message(self, sender: str, content: str, publish_events: bool, persist: bool, send_time: datetime | None = None) -> None:
        message = ChatMessage(
            sender_name=sender,
            content=content,
            send_time=send_time or datetime.now()
        )
        if persist:
            await persistenceService.append_room_message(
                room_id=self.key,
                sender=sender,
                content=content,
                send_time=message.send_time.isoformat(),
            )
        self.messages.append(message)
        if publish_events:
            messageBus.publish(
                MessageBusTopic.ROOM_MSG_ADDED,
                room_name=self.name,
                room_id=self.key,
                sender=sender,
                content=content,
                time=message.send_time.isoformat(),
            )

        if not self.agents:
            return

        self._apply_turn_logic(sender, publish_events=publish_events)

    def _apply_turn_logic(self, sender: str, publish_events: bool) -> None:
        # 1. 唤醒检查：如果房间已停止（无论原因），任何新消息都将重置轮次并恢复调度
        was_idle = (self._state == RoomState.IDLE)
        if was_idle:
            logger.info(f"检测到房间 {self.key} 的活动 ({sender})，重置轮次计数器并唤醒房间")
            self._turn_index = 0
            self._round_skipped = set()
            self._current_turn_has_content = False
            self._state = RoomState.SCHEDULING

        # 2. 只有当前顺序发言人说话，才标记本轮有内容。不再自动推进
        current_expected: Optional[str] = self.get_current_turn_agent()
        if sender == current_expected:
            self._current_turn_has_content = True
        else:
            logger.info(f"房间 {self.key} 收到来自 {sender} 的插话，保持当前发言位 (当前应轮到 {current_expected})")

        # 3. 只要有真实消息（非系统消息），就清空跳过记录，让所有人重新有机会回应
        if sender != "system" and self._round_skipped:
            self._round_skipped = set()

        # 4. 如果刚才从 IDLE 唤醒，我们需要手动重发当前轮次事件以重启循环
        if was_idle and publish_events:
            self._publish_current_turn()

    def finish_turn(self, sender: str = None) -> bool:
        """结束当前发言人的轮次。通常由 Agent 在 finish_chat_turn 工具中调用。
        返回 True 表示操作成功，False 表示被拒绝（sender 不是当前发言人）。
        """
        current_expected: Optional[str] = self.get_current_turn_agent()

        if sender and sender != current_expected:
            logger.warning(f"拒绝结束轮次申请：{sender} 并非当前发言人 ({current_expected})")
            return False

        logger.info(f"房间 {self.key} 由 {current_expected} 结束本轮行动 (has_content={self._current_turn_has_content})")

        # 如果本轮没说话，记录为跳过
        if not self._current_turn_has_content:
            self._round_skipped.add(current_expected)

        self._current_turn_has_content = False
        self._advance_turn(publish_events=True)
        return True

    def skip_turn(self, sender: str = None) -> bool:
        """(已废弃，建议使用 finish_turn) 跳过当前发言人的轮次。
        为了兼容性暂时保留，逻辑等同于 finish_turn 且强制标记为未发言。
        """
        self._current_turn_has_content = False
        return self.finish_turn(sender=sender)

    def get_current_turn_agent(self) -> Optional[str]:
        """返回当前理论上应该发言的 Agent 名（忽略 IDLE 状态）。"""
        if not self.agents:
            return None
        return self.agents[self._turn_pos]

    def _publish_current_turn(self) -> None:
        """发布当前轮次的发言事件。"""
        next_name: Optional[str] = self.get_current_turn_agent()
        if next_name:
            messageBus.publish(
                MessageBusTopic.ROOM_AGENT_TURN,
                agent_name=next_name,
                room_name=self.name,
                room_id=self.key,
                team_name=self.team_name,
            )

    def _advance_turn(self, publish_events: bool) -> None:
        """推进轮次位置索引。内部私有方法。"""
        if not self.agents:
            return

        self._turn_pos += 1

        # 1. 检查是否达到轮次边界
        if self._turn_pos >= len(self.agents):
            self._turn_index += 1
            self._turn_pos = 0

            # 正常达到最大轮次则停止
            if self._turn_index >= self._max_turns:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
                return

        # 2. 检查是否所有 AI Agent 均已跳过（自上次有消息以来）
        # 如果是，则立即停止调度，不再移动到下一位
        ai_agents = set(a for a in self.agents if a != SpecialAgent.OPERATOR)
        if ai_agents and ai_agents.issubset(self._round_skipped):
            self._state = RoomState.IDLE
            logger.info(f"房间 {self.key} 所有 AI 成员均已跳过发言（自上次消息以来），停止调度")
            return

        # 3. 正常发布下一位成员的发言事件
        if publish_events:
            self._publish_current_turn()

    def start_scheduling(self) -> None:
        if self._state == RoomState.SCHEDULING:
            self._publish_current_turn()

    def inject_history_messages(self, messages: List[ChatMessage]) -> None:
        self.messages = list(messages)

    def inject_agent_read_index(self, agent_read_index: Dict[str, int]) -> None:
        self._agent_read_index = dict(agent_read_index)

    def export_agent_read_index(self) -> Dict[str, int]:
        return dict(self._agent_read_index)

    def mark_all_messages_read(self) -> None:
        tail = len(self.messages)
        self._agent_read_index = {name: tail for name in self.agents}

    def rebuild_state_from_history(self) -> None:
        if not self.agents or self._max_turns <= 0:
            self._state = RoomState.IDLE
            return

        self._turn_index = 0
        self._turn_pos = 0
        self._round_skipped = set()
        self._state = RoomState.SCHEDULING

        for msg in self.messages:
            self._apply_turn_logic(msg.sender_name, publish_events=False)

    async def _persist_read_index(self) -> None:
        await persistenceService.save_room(self.key, self._agent_read_index)

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

    def build_initial_system_message(self) -> str:
        member_list_str = "、".join(self.agents)
        msg = f"{self.name} 房间已经创建，当前房间成员：{member_list_str}"
        if self.initial_topic:
            msg += f"\n本房间初始话题：{self.initial_topic}"
        return msg


_rooms: Dict[str, ChatRoom] = {}


async def startup() -> None:
    """初始化房间服务，清空所有房间。"""
    _rooms.clear()


async def _create_room(
    team_name: str,
    name: str,
    members: List[str],
    initial_topic: str = "",
    room_type: RoomType = RoomType.GROUP,
    max_turns: int = 0,
    persist_initial_message: bool = True,
) -> None:
    """内部建房入口。"""
    room = ChatRoom(name=name, team_name=team_name, initial_topic=initial_topic,
                    room_type=room_type, members=members, max_turns=max_turns)
    room_key = room.key
    _rooms[room_key] = room

    logger.info(f"创建并初始化聊天室: key={room_key}, type={room_type.value}, 成员={members}")
    if max_turns > 0:
        logger.info(f"初始化轮次配置: room={room_key}, max_turns={max_turns}")

    member_list_str = "、".join(members)
    msg = f"{name} 房间已经创建，当前房间成员：{member_list_str}"
    if initial_topic:
        msg += f"\n本房间初始话题：{initial_topic}"
    await room._append_message("system", msg, publish_events=True, persist=persist_initial_message)


async def create_room(team_name: str, name: str, members: List[str], initial_topic: str = "", room_type: RoomType = RoomType.GROUP, max_turns: int = 0) -> None:
    """创建并初始化一个聊天室。若 max_turns > 0 则启动轮次调度，发布系统公告。"""
    await _create_room(
        team_name=team_name,
        name=name,
        members=members,
        initial_topic=initial_topic,
        room_type=room_type,
        max_turns=max_turns,
        persist_initial_message=True,
    )


async def create_rooms(teams_config: list) -> None:
    """遍历所有 team，根据 rooms 配置批量创建聊天室。

    批量启动路径始终先生成初始化消息；若后续启用持久化恢复，恢复出的历史消息
    会覆盖这段启动期内存消息，从而保持最终房间状态与持久化一致。
    """
    for team in teams_config:
        team_name = team["name"]
        for room in team["rooms"]:
            await _create_room(
                team_name=team_name,
                name=room["name"],
                members=room["members"],
                initial_topic=room.get("initial_topic", ""),
                room_type=RoomType(room.get("type", "group")),
                max_turns=room.get("max_turns", 0),
                persist_initial_message=False,
            )


def get_member_names(team_name: str, room_name: str) -> List[str]:
    """返回聊天室的参与者名列表。"""
    return get_room(f"{room_name}@{team_name}").agents


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
    room: Optional[ChatRoom] = _rooms.get(room_key)
    if room is None:
        raise RuntimeError(f"聊天室 '{room_key}' 不存在")
    return room


def get_all_rooms() -> List[ChatRoom]:
    """返回所有聊天室实例列表。"""
    return list(_rooms.values())


def shutdown() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()


async def refresh_rooms_for_team(team_name: str, teams_config: list) -> None:
    """根据新的 Team 配置刷新聊天室。"""
    # 获取目标 Team 的新配置
    target_config = next((c for c in teams_config if c["name"] == team_name), None)
    if target_config is None:
        logger.warning(f"无法刷新聊天室: Team '{team_name}' 不存在于配置中")
        return

    # 先关闭该 Team 的所有现有聊天室
    await close_team_rooms(team_name)

    # 根据新配置重新创建聊天室
    for room in target_config.get("rooms", []):
        await _create_room(
            team_name=team_name,
            name=room["name"],
            members=room.get("members", []),
            initial_topic=room.get("initial_topic", ""),
            room_type=RoomType(room.get("type", "group")),
            max_turns=room.get("max_turns", 0),
            persist_initial_message=False,
        )

    logger.info(f"Team '{team_name}' 的聊天室已刷新，共 {len(target_config.get('rooms', []))} 个房间")


async def close_team_rooms(team_name: str) -> None:
    """关闭指定 Team 的所有聊天室。"""
    to_close = [key for key in _rooms.keys() if key.endswith(f"@{team_name}")]
    for room_key in to_close:
        del _rooms[room_key]
    logger.info(f"Team '{team_name}' 的 {len(to_close)} 个聊天室已关闭")
