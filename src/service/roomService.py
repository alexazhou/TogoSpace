from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from dal.db import gtRoomManager, gtTeamManager
from service import messageBus, persistenceService
from util.configTypes import TeamConfig, TeamRoomConfig
from model.coreModel.gtCoreChatModel import GtCoreChatMessage
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent

logger = logging.getLogger(__name__)


def _normalize_members(members: Sequence[str | SpecialAgent] | None) -> List[str]:
    if not members:
        return []
    return [member.name if isinstance(member, SpecialAgent) else member for member in members]


def _same_speaker(left: str | None, right: str | None) -> bool:
    """比较两个发言者标识。

    对 SpecialAgent（例如 Operator）使用枚举语义比较，避免字符串大小写/命名差异导致误判；
    其他普通 Agent 仍保持原有字符串精确比较。
    """
    if left is None or right is None:
        return left == right

    left_special = SpecialAgent.value_of(left)
    right_special = SpecialAgent.value_of(right)
    if left_special is not None or right_special is not None:
        return left_special is not None and left_special == right_special
    return left == right


def _infer_room_type(members: Sequence[str]) -> RoomType:
    normalized = _normalize_members(members)
    ai_count = len([m for m in normalized if SpecialAgent.value_of(m) != SpecialAgent.OPERATOR])
    if any(SpecialAgent.value_of(m) == SpecialAgent.OPERATOR for m in normalized) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


@dataclass
class ChatContext:
    """工具调用时注入的上下文，包含当前 Agent 和聊天室信息。"""
    agent_name: str
    team_name: str
    chat_room: ChatRoom


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    def __init__(self, team: GtTeam, room: GtRoom, members: List[str] = None):
        self.room_id: int = room.id  # 数据库主键 ID
        self.team_id: int = team.id  # 所属 Team 的数据库主键 ID
        self.name: str = room.name  # 房间名称
        self.team_name: str = team.name  # 所属 Team
        self.room_type: RoomType = room.type  # 房间类型（私有/群聊）
        self.messages: List[GtCoreChatMessage] = []  # 消息历史记录
        self.initial_topic: str = room.initial_topic  # 初始话题
        self.members: List[str] = _normalize_members(members)  # 房间参与者名单（包含 Operator 和 AI Agents）

        self._agent_read_index: Dict[str, int] = {}  # 每个 Agent 的消息读取进度
        self._turn_index: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._max_turns: int = room.max_turns  # 最大允许轮次
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.INIT  # 房间当前的调度状态
        self._state_after_init: RoomState = RoomState.SCHEDULING if self.members and room.max_turns > 0 else RoomState.IDLE
        self._round_skipped: set = set()  # 当前轮次已跳过发言的成员名单
        self._current_turn_has_content: bool = False  # 当前发言人是否已发送内容

    @property
    def key(self) -> str:
        return f"{self.name}@{self.team_name}"

    @property
    def state(self) -> RoomState:
        return self._state

    async def get_unread_messages(self, agent_name: str) -> List[GtCoreChatMessage]:
        """返回 agent_name 尚未读取的新消息，并推进其读取位置。"""
        read_idx = self._agent_read_index.get(agent_name, 0)
        new_msgs = self.messages[read_idx:]
        self._agent_read_index[agent_name] = len(self.messages)
        if self._state != RoomState.INIT:
            await persistenceService.save_room_runtime(self.room_id, self._agent_read_index)
        return new_msgs

    async def add_message(self, sender: str, content: str, send_time: datetime | None = None) -> None:
        message = GtCoreChatMessage(
            sender_name=sender,
            content=content,
            send_time=send_time or datetime.now()
        )
        self.messages.append(message)

        if self._state == RoomState.INIT:
            return

        await persistenceService.append_room_message(
            room_id=self.room_id,
            sender=sender,
            content=content,
            send_time=message.send_time.isoformat(),
        )

        messageBus.publish(
            MessageBusTopic.ROOM_MSG_ADDED,
            room_id=self.room_id,
            room_name=self.name,
            room_key=self.key,
            team_name=self.team_name,
            sender=sender,
            content=content,
            time=message.send_time.isoformat(),
        )
        if self.members:
            self._update_turn_state_on_message(sender)

    def _update_turn_state_on_message(self, sender: str) -> None:
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
        if _same_speaker(sender, current_expected):
            self._current_turn_has_content = True
        else:
            logger.info(f"房间 {self.key} 收到来自 {sender} 的插话，保持当前发言位 (当前应轮到 {current_expected})")

        # 3. 只要有真实消息（非系统消息），就清空跳过记录，让所有人重新有机会回应
        if sender != "system" and self._round_skipped:
            self._round_skipped = set()

        # 4. 如果刚才从 IDLE 唤醒，我们需要手动重发当前轮次事件以重启循环
        if was_idle:
            self._publish_current_turn()

    def finish_turn(self, sender: str | None = None) -> bool:
        """结束当前发言人的轮次。通常由 Agent 在 finish_chat_turn 工具中调用。
        返回 True 表示操作成功，False 表示被拒绝（sender 不是当前发言人）。
        """
        if self._state == RoomState.INIT:
            logger.warning(f"房间 {self.key} 仍处于 INIT，拒绝结束轮次")
            return False

        current_expected: Optional[str] = self.get_current_turn_agent()

        if sender and not _same_speaker(sender, current_expected):
            logger.warning(f"拒绝结束轮次申请：{sender} 并非当前发言人 ({current_expected})")
            return False

        logger.info(f"房间 {self.key} 由 {current_expected} 结束本轮行动 (has_content={self._current_turn_has_content})")

        # 如果本轮没说话，记录为跳过
        if not self._current_turn_has_content:
            self._round_skipped.add(current_expected)

        self._current_turn_has_content = False
        self._update_turn_state_on_finish()
        return True

    def get_current_turn_agent(self) -> Optional[str]:
        """返回当前理论上应该发言的 Agent 名（忽略 IDLE 状态）。"""
        if not self.members:
            return None
        return self.members[self._turn_pos]

    def _publish_current_turn(self) -> None:
        """发布当前轮次的发言事件。"""
        next_name: Optional[str] = self.get_current_turn_agent()
        if next_name:
            messageBus.publish(
                MessageBusTopic.ROOM_AGENT_TURN,
                agent_name=next_name,
                room_id=self.room_id,
                room_name=self.name,
                room_key=self.key,
                team_name=self.team_name,
            )

    def _update_turn_state_on_finish(self) -> None:
        """结束当前发言后，推进并更新轮次状态。"""
        if not self.members:
            return

        self._turn_pos += 1

        # 1. 检查是否达到轮次边界
        if self._turn_pos >= len(self.members):
            self._turn_index += 1
            self._turn_pos = 0

            # 正常达到最大轮次则停止
            if self._turn_index >= self._max_turns:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
                return

        # 2. 检查是否所有 AI Agent 均已跳过（自上次有消息以来）
        # 如果是，则立即停止调度，不再移动到下一位
        ai_agents = set(a for a in self.members if SpecialAgent.value_of(a) != SpecialAgent.OPERATOR)
        if ai_agents and ai_agents.issubset(self._round_skipped):
            self._state = RoomState.IDLE
            logger.info(f"房间 {self.key} 所有 AI 成员均已跳过发言（自上次消息以来），停止调度")
            return

        # 3. 正常发布下一位成员的发言事件
        self._publish_current_turn()

    def activate_scheduling(self) -> bool:
        """激活/重发调度。

        - INIT: 先切到恢复后的目标状态，再按需发布当前轮次
        - SCHEDULING: 直接重发当前轮次
        - IDLE: 不做任何操作

        返回是否发生了 INIT -> 非 INIT 的状态切换。
        """
        changed = False
        if self._state == RoomState.INIT:
            self._state = self._state_after_init
            changed = True

        if self._state == RoomState.SCHEDULING:
            self._publish_current_turn()

        return changed

    def inject_runtime_state(
        self,
        messages: List[GtCoreChatMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
    ) -> None:
        if messages is not None:
            self.messages = list(messages)
        if agent_read_index is not None:
            self._agent_read_index = dict(agent_read_index)

    def export_agent_read_index(self) -> Dict[str, int]:
        return dict(self._agent_read_index)

    def mark_all_messages_read(self) -> None:
        tail = len(self.messages)
        self._agent_read_index = {name: tail for name in self.members}

    def rebuild_state_from_history(self) -> None:
        keep_init = (self._state == RoomState.INIT)

        if not self.members or self._max_turns <= 0:
            self._state_after_init = RoomState.IDLE
            if keep_init:
                self._state = RoomState.INIT
            else:
                self._state = RoomState.IDLE
            return

        self._turn_index = 0
        self._turn_pos = 0
        self._round_skipped = set()
        self._state = RoomState.SCHEDULING

        for msg in self.messages:
            self._update_turn_state_on_message(msg.sender_name)

        self._state_after_init = self._state
        if keep_init:
            self._state = RoomState.INIT

    def format_log(self) -> str:
        lines = [f"=== {self.key} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.send_time.isoformat()}] {msg.sender_name}: {msg.content}")
        return "\n".join(lines)

    def build_initial_system_message(self) -> str:
        member_list_str = "、".join(self.members)
        msg = f"{self.name} 房间已经创建，当前房间成员：{member_list_str}"
        if self.initial_topic:
            msg += f"\n本房间初始话题：{self.initial_topic}"
        return msg


_rooms: Dict[str, ChatRoom] = {}  # room_key -> ChatRoom
_rooms_by_id: Dict[int, ChatRoom] = {}


def _room_key(team_name: str, room_name: str) -> str:
    return f"{room_name}@{team_name}"


def _iter_team_rooms(team_config: TeamConfig) -> list[TeamRoomConfig]:
    return team_config.preset_rooms


async def startup() -> None:
    """初始化房间服务，清空所有房间。"""
    _rooms.clear()
    _rooms_by_id.clear()


def get_room_by_key(room_key: str) -> ChatRoom:
    """通过 room_key（room_name@team_name）返回聊天室实例。"""
    room = _rooms.get(room_key)
    if room is None:
        raise RuntimeError(f"聊天室 '{room_key}' 不存在")
    return room


def get_room(room_id: int) -> ChatRoom | None:
    """通过数据库主键 room_id 返回聊天室实例，不存在时返回 None。"""
    return _rooms_by_id.get(room_id)


def get_all_rooms() -> List[ChatRoom]:
    """返回所有聊天室实例列表。"""
    return list(_rooms.values())


def shutdown() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()
    _rooms_by_id.clear()


async def _create_room(
    room_id: int | None,
    team_name: str,
    name: str,
    members: List[str],
    initial_topic: str = "",
    room_type: RoomType = RoomType.GROUP,
    max_turns: int = 0,
) -> None:
    """内部建房入口。"""
    # 1. 从 DB 查找 team_id 和已有 room_id
    team_row = await gtTeamManager.get_team(team_name)
    if team_row is None:
        team_row = GtTeam(
            id=0,
            name=team_name,
        )

    room_row: GtRoom | None = None

    if room_id is None:
        room_row = await gtRoomManager.ensure_room_by_key(
            team_id=team_row.id,
            room_name=name,
            room_type=room_type,
            initial_topic=initial_topic,
            max_turns=max_turns,
        )
    else:
        room_row = GtRoom(
            id=room_id,
            team_id=team_row.id,
            name=name,
            type=room_type,
            initial_topic=initial_topic,
            max_turns=max_turns,
            agent_read_index=None,
            updated_at=GtRoom._now_iso(),
        )

    resolved_room_id = room_row.id
    room = ChatRoom(team=team_row, room=room_row, members=members)
    room_key = room.key
    _rooms[room_key] = room
    _rooms_by_id[resolved_room_id] = room

    normalized_members = room.members

    logger.info(f"创建并初始化聊天室: room_id={resolved_room_id}, type={room_type.name}, 成员={normalized_members}")
    if max_turns > 0:
        logger.info(f"初始化轮次配置: room_id={resolved_room_id}, max_turns={max_turns}")

    await room.add_message("system", room.build_initial_system_message())


async def create_room(team_name: str, name: str, members: List[str], initial_topic: str = "", room_type: RoomType = RoomType.GROUP, max_turns: int = 0) -> None:
    """创建并初始化一个聊天室。创建后房间处于 INIT，需由 service 层显式退出 INIT。"""
    await _create_room(
        room_id=None,
        team_name=team_name,
        name=name,
        members=members,
        initial_topic=initial_topic,
        room_type=room_type,
        max_turns=max_turns,
    )


async def create_rooms(teams_config: list[TeamConfig]) -> None:
    """遍历所有 team，根据 rooms 配置批量创建聊天室。

    批量启动路径始终先生成初始化消息；若后续启用持久化恢复，恢复出的历史消息
    会覆盖这段启动期内存消息，从而保持最终房间状态与持久化一致。
    """
    for team in teams_config:
        team_name = team.name
        for room in _iter_team_rooms(team):
            await _create_room(
                room_id=room.id,
                team_name=team_name,
                name=room.name,
                members=room.members,
                initial_topic=room.initial_topic,
                room_type=_infer_room_type(room.members),
                max_turns=room.max_turns,
            )


def get_member_names(room_id: int) -> List[str]:
    """返回聊天室的参与者名列表。"""
    room = get_room(room_id)
    return room.members if room is not None else []


def get_rooms_for_agent(team_id: int | None, agent_name: str) -> List[int]:
    """返回指定参与者所在的房间 room_id 列表。可选按 team 过滤。"""
    results = []
    for room in _rooms.values():
        if any(_same_speaker(agent_name, member_name) for member_name in room.members):
            if team_id is None or room.team_id == team_id:
                results.append(room.room_id)
    return results


async def refresh_rooms_for_team(team_id: int, teams_config: list[TeamConfig]) -> None:
    """根据新的 Team 配置刷新聊天室。"""
    team_row = await gtTeamManager.get_team_by_id(team_id)
    if team_row is None:
        logger.warning(f"无法刷新聊天室: Team ID '{team_id}' 不存在")
        return
    team_name = team_row.name

    # 获取目标 Team 的新配置
    target_config = next((c for c in teams_config if c.name == team_name), None)
    if target_config is None:
        logger.warning(f"无法刷新聊天室: Team '{team_name}' 不存在于配置中")
        return

    # 先关闭该 Team 的所有现有聊天室
    await close_team_rooms(team_id)

    # 根据新配置重新创建聊天室
    for room in _iter_team_rooms(target_config):
        room_config = await gtRoomManager.get_room_config(team_row.id, room.name)
        if room_config:
            await _create_room(
                room_id=room_config.id,
                team_name=team_name,
                name=room.name,
                members=room.members,
                initial_topic=room.initial_topic,
                room_type=_infer_room_type(room.members),
                max_turns=room.max_turns,
            )

    logger.info(f"Team '{team_name}' 的聊天室已刷新，共 {len(_iter_team_rooms(target_config))} 个房间")


def exit_init_rooms(team_name: str | None = None) -> int:
    """内部 API：批量退出 INIT（可按 team 过滤）。"""
    changed = 0
    for room in _rooms.values():
        if team_name is not None and room.team_name != team_name:
            continue
        if room.activate_scheduling():
            changed += 1
    return changed


async def close_team_rooms(team_id: int) -> None:
    """关闭指定 Team 的所有聊天室。"""
    to_close = [room_key for room_key, room in _rooms.items() if room.team_id == team_id]
    for room_key in to_close:
        room = _rooms.pop(room_key)
        _rooms_by_id.pop(room.room_id, None)
    logger.info(f"Team ID={team_id} 的 {len(to_close)} 个聊天室已关闭")
