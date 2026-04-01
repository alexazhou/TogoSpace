from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from dal.db import gtRoomManager, gtTeamManager, gtAgentManager
from service import messageBus, persistenceService
from util import configUtil
from util import assertUtil
from exception import TeamAgentException
from model.coreModel.gtCoreChatModel import GtCoreChatMessage
from model.dbModel.gtDept import DeptRoomSpec
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent

logger = logging.getLogger(__name__)


def resolve_room_max_turns(max_turns: int | None) -> int:
    if max_turns is not None:
        return max_turns
    return configUtil.get_app_config().setting.default_room_max_turns


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
    normalized = [member.name if isinstance(member, SpecialAgent) else member for member in members]
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

    # 特殊成员 ID
    SYSTEM_MEMBER_ID = int(SpecialAgent.SYSTEM.value)
    OPERATOR_MEMBER_ID = int(SpecialAgent.OPERATOR.value)

    def __init__(self, team: GtTeam, room: GtRoom, members: List[GtAgent] | None = None):
        self.room_id: int = room.id  # 数据库主键 ID
        self.team_id: int = team.id  # 所属 Team 的数据库主键 ID
        self.name: str = room.name  # 房间名称
        self.team_name: str = team.name  # 所属 Team
        self.room_type: RoomType = room.type  # 房间类型（私有/群聊）
        self.messages: List[GtCoreChatMessage] = []  # 消息历史记录
        self.initial_topic: str = room.initial_topic  # 初始话题
        self.tags: List[str] = room.tags or []  # 房间标签
        self._members: List[GtAgent] = members or []  # 房间参与者列表
        self._member_names: List[str] = [m.name for m in self._members]
        self._member_id_map: Dict[str, int] = {m.name: m.id for m in self._members}

        self._member_name_map: Dict[int, str] = {m.id: m.name for m in self._members}
        self._member_name_map[self.SYSTEM_MEMBER_ID] = SpecialAgent.SYSTEM.name
        self._member_name_map[self.OPERATOR_MEMBER_ID] = SpecialAgent.OPERATOR.name
        self._member_read_index: Dict[str, int] = {}  # 每个成员的消息读取进度（name 为 key）
        self._turn_count: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._max_turns: int = room.max_turns  # 最大允许轮次
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.INIT  # 房间当前的调度状态
        self._state_after_init: RoomState = RoomState.SCHEDULING if self._members and room.max_turns > 0 else RoomState.IDLE
        self._round_skipped_set: set = set()  # 当前轮次已跳过发言的成员名单
        self._current_turn_has_content: bool = False  # 当前发言人是否已发送内容

    @property
    def members(self) -> List[str]:
        """返回成员名称列表（向后兼容）。"""
        return [
            member_name
            for member_name in self._member_names
            if SpecialAgent.value_of(member_name) != SpecialAgent.SYSTEM
        ]

    def get_member_id(self, name: str) -> int:
        """根据成员名称获取 member_id。"""
        special_agent = SpecialAgent.value_of(name)
        if special_agent is not None:
            return int(special_agent.value)
        return self._member_id_map.get(name, 0)

    def can_post_message(self, sender: str) -> bool:
        """返回 sender 是否允许向当前房间写消息。"""
        if SpecialAgent.value_of(sender) == SpecialAgent.SYSTEM:
            return True

        for member_name in self._member_names:
            if _same_speaker(sender, member_name):
                return True

        return False

    @property
    def key(self) -> str:
        return f"{self.name}@{self.team_name}"

    @property
    def state(self) -> RoomState:
        return self._state

    async def get_unread_messages(self, agent_name: str) -> List[GtCoreChatMessage]:
        """返回 agent_name 尚未读取的新消息，并推进其读取位置。"""
        read_idx = self._member_read_index.get(agent_name, 0)
        new_msgs = self.messages[read_idx:]
        self._member_read_index[agent_name] = len(self.messages)
        if self._state != RoomState.INIT:
            id_keyed = {str(self.get_member_id(k)): v for k, v in self._member_read_index.items()}
            await persistenceService.save_room_runtime(self.room_id, id_keyed)
        return new_msgs

    async def add_message(self, sender: str, content: str, send_time: datetime | None = None) -> None:
        await self._append_message(sender, content, send_time=send_time)

    async def _append_message(
        self,
        sender: str,
        content: str,
        send_time: datetime | None = None,
        *,
        update_turn_state: bool = True,
    ) -> None:
        assertUtil.assertTrue(
            self.can_post_message(sender),
            error_message=f"sender '{sender}' is not a member of room '{self.key}'",
            error_code="sender_not_in_room",
        )
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
            agent_id=self.get_member_id(sender),
            content=content,
            send_time=message.send_time.isoformat(),
        )

        messageBus.publish(
            MessageBusTopic.ROOM_MSG_ADDED,
            room_id=self.room_id,
            room_name=self.name,
            room_key=self.key,
            team_id=self.team_id,
            team_name=self.team_name,
            sender=sender,
            content=content,
            time=message.send_time.isoformat(),
        )
        if update_turn_state and self._member_names:
            self._update_turn_state_on_message(sender)

    def _update_turn_state_on_message(self, sender: str) -> None:
        # 1. 唤醒检查：如果房间已停止（无论原因），任何新消息都将重置轮次并恢复调度
        was_idle = (self._state == RoomState.IDLE)
        if was_idle:
            logger.info(f"检测到房间 {self.key} 的活动 ({sender})，重置轮次计数器并唤醒房间")
            self._turn_count = 0
            self._round_skipped_set = set()
            self._current_turn_has_content = False
            self._state = RoomState.SCHEDULING

        # 2. 只有当前顺序发言人说话，才标记本轮有内容。不再自动推进
        current_expected: Optional[str] = self.get_current_turn_agent()
        if _same_speaker(sender, current_expected):
            self._current_turn_has_content = True
        else:
            logger.info(f"房间 {self.key} 收到来自 {sender} 的插话，保持当前发言位 (当前应轮到 {current_expected})")

        # 3. 只要有真实消息（非系统消息），就清空跳过记录，让所有人重新有机会回应
        if sender != SpecialAgent.SYSTEM.name and self._round_skipped_set:
            self._round_skipped_set = set()

        # 4. 如果刚才从 IDLE 唤醒，我们需要手动重发当前轮次事件以重启循环
        if was_idle:
            next_member = self._resolve_next_dispatchable_member()
            if next_member is not None:
                self._publish_current_turn(next_member)

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
            self._round_skipped_set.add(current_expected)

        self._current_turn_has_content = False

        if not self._member_names:
            return True

        if not self._go_next_turn():
            return True

        next_member = self._resolve_next_dispatchable_member()
        if next_member is not None:
            self._publish_current_turn(next_member)
        return True

    def get_current_turn_agent(self) -> Optional[str]:
        """返回当前理论上应该发言的 Agent 名（忽略 IDLE 状态）。"""
        if not self._member_names:
            return None
        return self._member_names[self._turn_pos]

    def _should_auto_skip_operator_turn(self, member_name: str | None) -> bool:
        return (
            member_name is not None
            and SpecialAgent.value_of(member_name) == SpecialAgent.OPERATOR
            and self.room_type == RoomType.GROUP
            and len(self._member_names) > 2
        )

    def _publish_current_turn(self, member_name: str) -> None:
        """仅发布指定成员的发言事件，不处理状态推进。"""
        messageBus.publish(
            MessageBusTopic.ROOM_MEMBER_TURN,
            member_name=member_name,
            room_id=self.room_id,
            room_name=self.name,
            room_key=self.key,
            team_name=self.team_name,
        )

    def _resolve_next_dispatchable_member(self) -> Optional[str]:
        """根据当前状态解析下一位可被调度的成员名；若应停止则返回 None。"""
        if not self._member_names:
            return None

        if self._try_stop_scheduling():
            return None

        while True:
            next_name: Optional[str] = self.get_current_turn_agent()

            if self._should_auto_skip_operator_turn(next_name):
                logger.info(f"房间 {self.key} 自动跳过人类操作者回合: member={next_name}")
                self._round_skipped_set.add(next_name)
                self._current_turn_has_content = False

                if not self._go_next_turn():
                    return None
                continue

            return next_name

    def _try_stop_scheduling(self) -> bool:
        """集中判断并应用停止条件；满足任一条件则切到 IDLE 并返回 True。"""
        if self._turn_count >= self._max_turns:
            if self._state != RoomState.IDLE:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
            return True

        ai_agents = {a for a in self._member_names if SpecialAgent.value_of(a) != SpecialAgent.OPERATOR}
        if ai_agents and ai_agents.issubset(self._round_skipped_set):
            if self._state != RoomState.IDLE:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 所有 AI 成员均已跳过发言（自上次消息以来），停止调度")
            return True
        return False

    def _go_next_turn(self) -> bool:
        """推进到下一发言位；若命中停止条件则返回 False。"""
        self._turn_pos = (self._turn_pos + 1) % len(self._member_names)

        # turn_pos 回到 0 代表跨轮（从最后一位回到首位）；
        # 只有在跨轮时才推进 turn_count。
        if self._turn_pos == 0:
            self._turn_count += 1

        return not self._try_stop_scheduling()

    async def activate_scheduling(self) -> bool:
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
            if not self.messages:
                await self._append_message(
                    SpecialAgent.SYSTEM.name,
                    self.build_initial_system_message(),
                    update_turn_state=False,
                )

        if self._state == RoomState.SCHEDULING:
            next_member = self._resolve_next_dispatchable_member()
            if next_member is not None:
                self._publish_current_turn(next_member)

        return changed

    def inject_runtime_state(
        self,
        messages: List[GtCoreChatMessage] | None = None,
        member_read_index: Dict[str, int] | None = None,
    ) -> None:
        if messages is not None:
            self.messages = list(messages)
        if member_read_index is not None:
            converted: Dict[str, int] = {}
            for k, v in member_read_index.items():
                try:
                    name = self._member_name_map.get(int(k), k)
                except (ValueError, TypeError):
                    name = k  # fallback: key is already a member name (legacy data)
                converted[name] = v
            self._member_read_index = converted

    def export_member_read_index(self) -> Dict[str, int]:
        return dict(self._member_read_index)

    def mark_all_messages_read(self) -> None:
        tail = len(self.messages)
        self._member_read_index = {name: tail for name in self._member_names}

    def rebuild_state_from_history(self) -> None:
        keep_init = (self._state == RoomState.INIT)

        if not self._member_names or self._max_turns <= 0:
            self._state_after_init = RoomState.IDLE
            if keep_init:
                self._state = RoomState.INIT
            else:
                self._state = RoomState.IDLE
            return

        self._turn_count = 0
        self._turn_pos = 0
        self._round_skipped_set = set()
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
        msg = f"系统提示: {self.name} 房间已经创建，当前房间成员：{member_list_str}"
        if self.initial_topic:
            msg += f"\n本房间初始话题：{self.initial_topic}"
        return msg

    def to_dict(self) -> dict:
        """返回用于 API 响应的字典表示，包含运行时状态。"""
        return {
            "room_id": self.room_id,
            "room_key": self.key,
            "room_name": self.name,
            "team_name": self.team_name,
            "room_type": self.room_type.name,
            "state": self._state.name,
            "members": list(self.members),
            "agent_ids": [self.get_member_id(member_name) for member_name in self.members],
            "tags": self.tags,
        }


_rooms: Dict[str, ChatRoom] = {}  # room_key -> ChatRoom
_rooms_by_id: Dict[int, ChatRoom] = {}


async def startup() -> None:
    """初始化房间服务，清空所有房间。"""
    _rooms.clear()
    _rooms_by_id.clear()


async def restore_state() -> None:
    """从数据库恢复所有房间的运行时状态。"""
    for room in get_all_rooms():
        room_msg_rows, member_read_index = await persistenceService.load_room_runtime(room.room_id)
        recovered_from_db = bool(room_msg_rows)
        restored_messages: list[GtCoreChatMessage] | None = None

        if room_msg_rows:
            restored_messages = [
                GtCoreChatMessage(
                    sender_name=room._member_name_map.get(row.agent_id, SpecialAgent.SYSTEM.name),
                    content=row.content,
                    send_time=datetime.fromisoformat(row.send_time),
                )
                for row in room_msg_rows
            ]

        if restored_messages is not None or member_read_index is not None:
            room.inject_runtime_state(
                messages=restored_messages,
                member_read_index=member_read_index,
            )
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()

        room.rebuild_state_from_history()


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


async def update_room_members(room_id: int, agent_ids: list[int]) -> None:
    room = await gtRoomManager.get_room_by_id(room_id)
    assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")

    room.agent_ids = agent_ids
    await gtRoomManager.save_room(room)


async def overwrite_dept_rooms(team_id: int, rooms: Sequence[DeptRoomSpec]) -> None:
    """按部门房间信息同步 DEPT 房间。

    行为约定：
    - 以 biz_id 作为幂等键，存在则更新，不存在则创建。
    - 每个目标房间都会同步成员列表为 spec.member_ids。
    - 最后会删除 team 下不在本次 biz_id 列表中的 DEPT 房间。
    """
    # 去重并固定“目标态”：同一 biz_id 仅保留最后一条 spec。
    by_biz_id: dict[str, DeptRoomSpec] = {room.biz_id: room for room in rooms}

    for spec in by_biz_id.values():
        # 1) 按 biz_id 查找目标房间，不存在则初始化一个待创建对象。
        existing = await gtRoomManager.get_room_by_biz_id(team_id, spec.biz_id)
        room = existing or GtRoom(
            team_id=team_id,
            name="",
            type=RoomType.GROUP,
            initial_topic="",
            max_turns=10,
            agent_ids=[],
            biz_id=spec.biz_id,
            tags=["DEPT"],
        )

        room.team_id = team_id
        room.name = spec.name
        room.type = RoomType.GROUP
        room.initial_topic = spec.initial_topic
        room.max_turns = resolve_room_max_turns(spec.max_turns)
        room.biz_id = spec.biz_id
        room.tags = ["DEPT"]

        # 2) 保存房间元信息，再覆盖成员列表。
        saved_room = await gtRoomManager.save_room(room)
        await update_room_members(saved_room.id, spec.member_ids)

    # 3) 清理不在目标态中的历史 DEPT 房间。
    await gtRoomManager.delete_rooms_by_biz_ids_not_in(team_id, list(by_biz_id.keys()))


async def create_team_rooms(team_id: int, rooms: Sequence[GtRoom]) -> None:
    """创建 team rooms：要求 team 还没有任何房间。"""
    existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    assertUtil.assertTrue(
        len(existing_rooms) == 0,
        error_message=f"team_id '{team_id}' already has rooms, use overwrite_team_rooms instead",
        error_code="TEAM_ROOMS_ALREADY_EXIST",
    )
    await batch_create_rooms(team_id, rooms)


async def batch_create_rooms(team_id: int, rooms: Sequence[GtRoom]) -> None:
    """批量创建房间（create-only）。若房间已存在则报错。"""
    room_list = list(rooms)
    seen_names: set[str] = set()
    for room in room_list:
        if room.id is not None:
            raise TeamAgentException(
                f"create-only 场景不允许传入 room.id: '{room.id}'",
                error_code="ROOM_ID_NOT_ALLOWED_ON_CREATE",
            )

        if room.name in seen_names:
            raise TeamAgentException(
                f"房间名称重复: '{room.name}'",
                error_code="ROOM_NAME_DUPLICATED",
            )
        seen_names.add(room.name)

    existing_rooms = await gtRoomManager.get_rooms_by_team_and_names(
        team_id,
        [room.name for room in room_list],
    )
    if existing_rooms:
        raise TeamAgentException(
            f"房间名称已存在: '{existing_rooms[0].name}'",
            error_code="ROOM_ALREADY_EXISTS",
        )

    for room in room_list:
        room.team_id = team_id
    await gtRoomManager.batch_save_rooms(room_list)


async def overwrite_team_rooms(team_id: int, rooms: Sequence[GtRoom]) -> None:
    """常规更新流程：按目标房间集创建/更新房间，并清理已移除房间。"""
    current_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    next_names = {room.name for room in rooms}
    next_ids = {room.id for room in rooms if room.id is not None}

    obsolete_room_ids = [
        room.id
        for room in current_rooms
        if room.id not in next_ids and room.name not in next_names and not room.biz_id
    ]
    for room_id in obsolete_room_ids:
        await gtRoomManager.delete_room(room_id)

    for room_input in rooms:
        room = await gtRoomManager.get_room_by_team_and_id_or_name(team_id, room_input.id, room_input.name)
        if room is None:
            room = GtRoom(
                team_id=team_id,
                name="",
                type=RoomType.GROUP,
                initial_topic="",
                max_turns=10,
                agent_ids=[],
                biz_id=None,
                tags=[],
            )

        room.team_id = team_id
        room.name = room_input.name
        room.type = room_input.type
        room.initial_topic = room_input.initial_topic
        room.max_turns = room_input.max_turns
        room.biz_id = room_input.biz_id
        room.tags = list(room_input.tags or [])
        room.agent_ids = list(room_input.agent_ids or [])
        await gtRoomManager.save_room(room)


async def _load_room(
    team_row: GtTeam,
    room_row: GtRoom,
    agent_ids: List[int],
) -> None:
    """将数据库房间装载到运行态。"""
    room_members = await gtAgentManager.get_team_agents_by_ids(team_row.id, agent_ids, include_special=True)

    room = ChatRoom(team=team_row, room=room_row, members=room_members)
    _rooms[room.key] = room
    _rooms_by_id[room.room_id] = room

    logger.info(f"创建并初始化聊天室: room_id={room.room_id}, type={room.room_type.name}, 成员={[member.name for member in room_members]}")
    if room_row.max_turns > 0:
        logger.info(f"初始化轮次配置: room_id={room.room_id}, max_turns={room_row.max_turns}")

async def ensure_room_record(team_name: str, name: str, members: List[str], initial_topic: str = "", room_type: RoomType = RoomType.GROUP, max_turns: int = 0) -> None:
    """确保房间记录存在并装载运行态。创建后房间处于 INIT，需由 service 层显式退出 INIT。"""
    team_row = await gtTeamManager.get_team(team_name)
    assert team_row is not None, f"Team '{team_name}' 不存在，调用 ensure_room_record 前应先创建 Team"
    agent_ids = list(map(
        lambda agent: agent.id,
        await gtAgentManager.get_team_agents_by_names(
            team_row.id,
            members,
            include_special=True,
        ),
    ))
    room_row = await gtRoomManager.get_room_by_team_and_name(team_row.id, name)
    if room_row is None:
        room_row = GtRoom(
            team_id=team_row.id,
            name=name,
            type=room_type,
            initial_topic=initial_topic,
            max_turns=max_turns,
            agent_ids=[],
            biz_id=None,
            tags=[],
        )
    else:
        room_row.type = room_type
        room_row.initial_topic = initial_topic
        room_row.max_turns = max_turns
    room_row.agent_ids = list(agent_ids)
    room_row = await gtRoomManager.save_room(room_row)
    await _load_room(
        team_row=team_row,
        room_row=room_row,
        agent_ids=agent_ids,
    )


async def load_rooms_from_db() -> None:
    """从数据库装载所有聊天室到运行态。"""
    for team_row in await gtTeamManager.get_all_teams():
        for room_row in await gtRoomManager.get_rooms_by_team(team_row.id):
            await _load_room(
                team_row=team_row,
                room_row=room_row,
                agent_ids=room_row.agent_ids or [],
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


async def refresh_rooms_for_team(team_id: int) -> None:
    """根据数据库中的最新数据刷新指定 Team 的聊天室运行态。"""
    team_row = await gtTeamManager.get_team_by_id(team_id)
    if team_row is None:
        logger.warning(f"无法刷新聊天室: Team ID '{team_id}' 不存在")
        return

    # 先关闭该 Team 的所有现有聊天室
    await close_team_rooms(team_id)

    room_rows = await gtRoomManager.get_rooms_by_team(team_row.id)
    for room_row in room_rows:
        await _load_room(
            team_row=team_row,
            room_row=room_row,
            agent_ids=room_row.agent_ids or [],
        )

    logger.info(f"Team '{team_row.name}' 的聊天室已刷新，共 {len(room_rows)} 个房间")


async def activate_rooms(team_name: str | None = None) -> None:
    """统一激活入口：对目标房间调用 activate_scheduling（可按 team 过滤）。"""
    for room in _rooms.values():
        if team_name is not None and room.team_name != team_name:
            continue
        await room.activate_scheduling()


async def close_team_rooms(team_id: int) -> None:
    """关闭指定 Team 的所有聊天室。"""
    to_close = [room_key for room_key, room in _rooms.items() if room.team_id == team_id]
    for room_key in to_close:
        room = _rooms.pop(room_key)
        _rooms_by_id.pop(room.room_id, None)
    logger.info(f"Team ID={team_id} 的 {len(to_close)} 个聊天室已关闭")
