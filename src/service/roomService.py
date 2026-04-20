from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from dal.db import gtRoomManager, gtTeamManager, gtAgentManager, gtRoomMessageManager
from service import messageBus
from util import configUtil, i18nUtil
from util import assertUtil
from exception import TeamAgentException
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from model.dbModel.gtDept import DeptRoomSpec
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
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


def _infer_room_type(agent_names: Sequence[str]) -> RoomType:
    normalized = [agent.name if isinstance(agent, SpecialAgent) else agent for agent in agent_names]
    ai_count = len([m for m in normalized if SpecialAgent.value_of(m) != SpecialAgent.OPERATOR])
    if any(SpecialAgent.value_of(m) == SpecialAgent.OPERATOR for m in normalized) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


@dataclass
class ToolCallContext:
    """工具调用时注入的上下文，包含当前 Agent、工具名和聊天室信息。"""
    agent_name: str
    team_id: int
    chat_room: ChatRoom
    tool_name: str = ""


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    # 特殊 Agent ID
    SYSTEM_MEMBER_ID = int(SpecialAgent.SYSTEM.value)
    OPERATOR_MEMBER_ID = int(SpecialAgent.OPERATOR.value)

    def __init__(self, team: GtTeam, room: GtRoom, agents: List[GtAgent] | None = None):
        self.gt_room: GtRoom = room
        self.gt_team: GtTeam = team
        self.messages: List[GtCoreRoomMessage] = []  # 消息历史记录
        self._agents: List[GtAgent] = agents or []  # 房间参与者列表
        self._agent_ids: List[int] = [agent.id for agent in self._agents]  # agent_id 列表，调度逻辑频繁使用索引访问
        self._agent_read_index: Dict[int, int] = {}  # 每个 Agent 的消息读取进度（agent_id 为 key）
        self._turn_count: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.INIT  # 房间当前的调度状态
        self._round_skipped_set: set[int] = set()  # 当前轮次已跳过发言的 Agent ID 集合
        self._current_turn_has_content: bool = False  # 当前发言人是否已发送内容

    # ─── 从 gt_room / gt_team 派生的只读属性 ────────────────────

    @property
    def room_id(self) -> int:
        return self.gt_room.id

    @property
    def team_id(self) -> int:
        return self.gt_team.id

    @property
    def name(self) -> str:
        return self.gt_room.name

    @property
    def team_name(self) -> str:
        return self.gt_team.name

    @property
    def room_type(self) -> RoomType:
        return self.gt_room.type

    @property
    def initial_topic(self) -> str:
        return self.gt_room.initial_topic

    @property
    def tags(self) -> List[str]:
        return self.gt_room.tags or []

    @property
    def _max_turns(self) -> int:
        return self.gt_room.max_turns

    @property
    def agents(self) -> List[int]:
        """返回 Agent ID 列表（排除系统成员，用于 API 响应）。"""
        return [
            agent.id
            for agent in self._agents
            if agent.id != self.SYSTEM_MEMBER_ID
        ]

    @property
    def agent_names(self) -> List[str]:
        """返回 Agent 名称列表（排除系统成员，用于展示）。"""
        return [
            agent.name
            for agent in self._agents
            if agent.id != self.SYSTEM_MEMBER_ID
        ]

    def _get_agent_by_id(self, agent_id: int) -> GtAgent | None:
        """根据 agent_id 获取 GtAgent 对象。"""
        for agent in self._agents:
            if agent.id == agent_id:
                return agent
        return None

    def _get_agent_name(self, agent_id: int) -> str:
        """根据 agent_id 获取显示名称（从 i18n 解析）。"""
        if agent_id == self.SYSTEM_MEMBER_ID:
            return SpecialAgent.SYSTEM.name
        if agent_id == self.OPERATOR_MEMBER_ID:
            return SpecialAgent.OPERATOR.name
        agent = self._get_agent_by_id(agent_id)
        if agent is None:
            return str(agent_id)
        return self._get_agent_display_name(agent)

    def _get_agent_stable_name(self, agent_id: int) -> str:
        """根据 agent_id 获取稳定标识名（用于持久化和匹配）。"""
        if agent_id == self.SYSTEM_MEMBER_ID:
            return SpecialAgent.SYSTEM.name
        if agent_id == self.OPERATOR_MEMBER_ID:
            return SpecialAgent.OPERATOR.name
        agent = self._get_agent_by_id(agent_id)
        return agent.name if agent else str(agent_id)

    def get_agent_id_by_name(self, name: str) -> int | None:
        """根据 Agent 名称获取 agent_id。未找到时返回 None。"""
        special_agent = SpecialAgent.value_of(name)
        if special_agent is not None:
            return int(special_agent.value)
        for agent in self._agents:
            if agent.name == name:
                return agent.id
        return None

    def get_gt_agent(self, agent_id: int) -> GtAgent | None:
        """根据 agent_id 获取运行态房间中的 Agent 对象（包含 SpecialAgent）。"""
        return self._get_agent_by_id(agent_id)

    def can_post_message(self, sender_id: int) -> bool:
        """返回 sender_id 是否允许向当前房间写消息。"""
        return sender_id in self._agent_ids or sender_id == self.SYSTEM_MEMBER_ID

    @property
    def key(self) -> str:
        return f"{self.name}@{self.team_name}"

    @property
    def state(self) -> RoomState:
        return self._state

    async def get_unread_messages(self, agent_id: int) -> List[GtCoreRoomMessage]:
        """返回 agent_id 尚未读取的新消息，并推进其读取位置。"""
        read_idx = self._agent_read_index.get(agent_id, 0)
        new_msgs = self.messages[read_idx:]
        self._agent_read_index[agent_id] = len(self.messages)
        if self._state != RoomState.INIT:
            id_keyed = {str(k): v for k, v in self._agent_read_index.items()}
            await gtRoomManager.update_room_state(self.room_id, id_keyed, self._turn_pos)
        return new_msgs

    async def add_message(self, sender_id: int, content: str, send_time: datetime | None = None) -> None:
        """添加消息到房间。"""
        await self._append_message(sender_id, content, send_time=send_time)

    async def _append_message(
        self,
        sender_id: int,
        content: str,
        send_time: datetime | None = None,
        *,
        update_turn_state: bool = True,
    ) -> None:
        assertUtil.assertTrue(
            self.can_post_message(sender_id),
            error_message=f"sender_id '{sender_id}' is not an agent of room '{self.key}'",
            error_code="sender_not_in_room",
        )
        message = GtCoreRoomMessage(
            sender_id=sender_id,
            sender_name=self._get_agent_name(sender_id),
            content=content,
            send_time=send_time or datetime.now()
        )
        self.messages.append(message)

        if self._state == RoomState.INIT:
            return

        await gtRoomMessageManager.append_room_message(
            room_id=self.room_id,
            agent_id=sender_id,
            content=content,
            send_time=message.send_time.isoformat(),
        )

        messageBus.publish(
            MessageBusTopic.ROOM_MSG_ADDED,
            gt_room=self.gt_room,
            sender_id=sender_id,
            content=content,
            time=message.send_time.isoformat(),
        )
        if update_turn_state and self._agent_ids:
            self._update_turn_state_on_message(sender_id)

    def _update_turn_state_on_message(self, sender_id: int) -> None:
        # 1. 唤醒检查：如果房间已停止（无论原因），任何新消息都将重置轮次并恢复调度
        was_idle = (self._state == RoomState.IDLE)
        if was_idle:
            logger.info(f"检测到房间 {self.key} 的活动 ({self._get_agent_name(sender_id)}(agent_id={sender_id}))，重置轮次计数器并唤醒房间")
            self._turn_count = 0
            self._round_skipped_set = set()
            self._current_turn_has_content = False
            self._state = RoomState.SCHEDULING

        # 2. 只有当前顺序发言人说话，才标记本轮有内容。不再自动推进
        current_expected = self._get_current_turn_agent_id()
        if sender_id == current_expected:
            self._current_turn_has_content = True
        else:
            logger.info(f"房间 {self.key} 收到来自 {self._get_agent_name(sender_id)}(agent_id={sender_id}) 的插话，保持当前发言位 (当前应轮到 {self._get_agent_name(current_expected)}(agent_id={current_expected}))")

        # 3. 只要有真实消息（非系统消息），就清空跳过记录，让所有人重新有机会回应
        if sender_id != self.SYSTEM_MEMBER_ID and self._round_skipped_set:
            self._round_skipped_set = set()

        # 4. 如果刚才从 IDLE 唤醒，我们需要手动重发当前轮次事件以重启循环
        if was_idle:
            next_agent_id = self._resolve_next_dispatchable_agent()
            if next_agent_id is not None:
                self._publish_room_status(need_scheduling=True)
            else:
                # 无可调度 Agent（如等待 OPERATOR 输入），仍需广播状态变更
                self._publish_room_status()

    async def finish_turn(self, agent_id: int) -> bool:
        """结束当前发言人的轮次。通常由 Agent 在 finish_chat_turn 工具中调用。

        返回 True 表示操作成功，False 表示被拒绝（agent 不是当前发言人）。
        """
        assertUtil.assertNotNull(agent_id, error_message=f"agent_id 不能为空, room={self.key}")

        if self._state == RoomState.INIT:
            logger.warning(f"房间 {self.key} 仍处于 INIT，拒绝结束轮次")
            return False

        current_expected = self._get_current_turn_agent_id()

        if agent_id != current_expected:
            logger.warning(f"房间 {self.key} 拒绝结束轮次申请：{self._get_agent_name(agent_id)}(agent_id={agent_id}) 并非当前发言人 {self._get_agent_name(current_expected)}(agent_id={current_expected})")
            return False

        logger.info(
            "房间 %s 由 %s(agent_id=%d) 结束本轮行动 (has_content=%s, turn_pos=%d/%d, turn_count=%d)",
            self.key, self._get_agent_name(current_expected), current_expected,
            self._current_turn_has_content, self._turn_pos, len(self._agent_ids), self._turn_count,
        )

        # 如果本轮没说话，记录为跳过
        if not self._current_turn_has_content:
            self._round_skipped_set.add(current_expected)

        self._current_turn_has_content = False

        if not self._agent_ids:
            return True

        self._go_next_turn()
        await self._persist_turn_pos()
        next_agent_id = self._resolve_next_dispatchable_agent()
        if next_agent_id is not None:
            self._publish_room_status(need_scheduling=True)
        else:
            # 停止条件已由 _should_stop_scheduling() 处理（切 IDLE + 发布）；
            # 若状态仍为 SCHEDULING，说明是 OPERATOR 等待情形，切换到 IDLE
            if self._state == RoomState.SCHEDULING:
                self._state = RoomState.IDLE
                self._publish_room_status()
        return True

    def _get_current_turn_agent_id(self) -> int:
        """返回当前理论上应该发言的 Agent ID（内部方法，忽略 IDLE 状态）。"""
        assert self._agent_ids, f"房间 {self.key} 没有任何参与者"
        return self._agent_ids[self._turn_pos]

    async def _persist_turn_pos(self) -> None:
        """将 _turn_pos 持久化到数据库（在 finish_turn 后调用）。"""
        id_keyed = {str(k): v for k, v in self._agent_read_index.items()}
        await gtRoomManager.update_room_state(self.room_id, id_keyed, self._turn_pos)

    def get_current_turn_agent(self) -> GtAgent:
        """返回当前理论上应该发言的 GtAgent 对象（忽略 IDLE 状态）。"""
        return self.get_gt_agent(self._get_current_turn_agent_id())

    def get_current_turn_agent_name(self) -> str:
        """返回当前理论上应该发言的 Agent 名称（用于日志和测试）。"""
        return self.get_current_turn_agent().name

    def _should_auto_skip_agent_turn(self) -> bool:
        """判断当前发言位是否应被自动跳过（不等待外部输入）。

        仅针对 GROUP 房间中的 OPERATOR：当成员数 > 2 时，OPERATOR 的回合会被自动跳过，
        直接推进到下一位 AI 成员，无需等待人类输入。

        返回 True 表示应自动跳过并推进；返回 False 表示需等待该成员完成本轮。
        """
        agent_id = self._get_current_turn_agent_id()
        return (
            agent_id == self.OPERATOR_MEMBER_ID
            and self.room_type == RoomType.GROUP
            and len(self._agent_ids) > 2
        )

    def _is_special_agent(self, agent_id: int | None) -> bool:
        """判断是否为特殊成员（SYSTEM/OPERATOR）。"""
        return agent_id in (self.SYSTEM_MEMBER_ID, self.OPERATOR_MEMBER_ID)

    def _publish_room_status(self, need_scheduling: bool = False) -> None:
        """广播房间状态快照（state + 当前发言人）给前端。不推送 INIT 状态。"""
        if self._state == RoomState.INIT:
            return
        current_turn_agent = (
            self.get_gt_agent(self._get_current_turn_agent_id())
            if self._state == RoomState.SCHEDULING and self._agent_ids
            else None
        )
        messageBus.publish(
            MessageBusTopic.ROOM_STATUS_CHANGED,
            gt_room=self.gt_room,
            state=self._state,
            current_turn_agent=current_turn_agent,
            need_scheduling=need_scheduling,
        )

    def _resolve_next_dispatchable_agent(self) -> Optional[int]:
        """解析下一位可发布 ROOM_AGENT_TURN 的普通 Agent ID。

        处理流程：
        1. 先检查停止条件，若满足则返回 None
        2. 循环遍历当前发言位：
           - 若命中 _should_auto_skip_agent_turn()，自动跳过并推进到下一位
           - 若当前发言位是 SpecialAgent（非自动跳过场景），返回 None 等待外部输入
           - 若是普通 Agent，返回其 ID 供上层发布事件

        返回 None 表示当前不应发布调度事件，原因可能是：
        - 房间已命中停止条件（_should_stop_scheduling 返回 True）
        - 当前发言位是需要等待外部输入的 SpecialAgent（如 PRIVATE 房间的 OPERATOR）
        """
        if not self._agent_ids:
            return None

        if self._should_stop_scheduling():
            return None

        while True:
            next_agent = self.get_current_turn_agent()
            next_id = next_agent.id if next_agent else None

            if self._should_auto_skip_agent_turn():
                logger.info(f"房间 {self.key} 自动跳过人类操作者回合: {self._get_agent_name(next_id)}(agent_id={next_id})")
                if next_id is not None:
                    self._round_skipped_set.add(next_id)
                self._current_turn_has_content = False

                self._go_next_turn()
                if self._should_stop_scheduling():
                    return None
                continue

            if self._is_special_agent(next_id):
                logger.info(
                    "当前发言位为特殊成员，等待外部输入，不发布 ROOM_AGENT_TURN: room=%s, %s(agent_id=%s)",
                    self.key,
                    self._get_agent_name(next_id),
                    next_id,
                )
                return None

            return next_id

    def _should_stop_scheduling(self) -> bool:
        """集中判断并应用停止条件；满足任一条件则切到 IDLE 并返回 True。"""
        if self._turn_count >= self._max_turns:
            if self._state != RoomState.IDLE:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
                self._publish_room_status()
            return True

        # 获取所有非 OPERATOR 的 AI agent ID
        ai_agent_ids = {aid for aid in self._agent_ids if aid != self.OPERATOR_MEMBER_ID}
        if ai_agent_ids and ai_agent_ids.issubset(self._round_skipped_set):
            if self._state != RoomState.IDLE:
                self._state = RoomState.IDLE
                logger.info(f"房间 {self.key} 所有 AI 成员均已跳过发言（自上次消息以来），停止调度")
                self._publish_room_status()
            return True
        return False

    def _go_next_turn(self) -> None:
        """推进到下一发言位。"""
        self._turn_pos = (self._turn_pos + 1) % len(self._agent_ids)

        # turn_pos 回到 0 代表跨轮（从最后一位回到首位）；
        # 只有在跨轮时才推进 turn_count。
        if self._turn_pos == 0:
            self._turn_count += 1

    async def activate_scheduling(self) -> bool:
        """激活/重发调度。

        - INIT: 根据当前条件决定目标状态（SCHEDULING 或 IDLE），发送初始消息
        - SCHEDULING: 直接重发当前轮次
        - IDLE: 不做任何操作

        返回是否发生了 INIT -> 非 INIT 的状态切换。
        """
        changed = False
        if self._state == RoomState.INIT:
            self._state = (
                RoomState.SCHEDULING
                if self._agent_ids and self._max_turns > 0
                else RoomState.IDLE
            )
            changed = True
            logger.info(
                "[%s] 房间激活: INIT -> %s (agents=%d, max_turns=%d)",
                self.key, self._state.name, len(self._agent_ids), self._max_turns,
            )
            if not self.messages:
                await self._append_message(
                    self.SYSTEM_MEMBER_ID,
                    self.build_initial_system_message(),
                    update_turn_state=False,
                )

        if self._state == RoomState.SCHEDULING:
            next_agent_id = self._resolve_next_dispatchable_agent()
            if next_agent_id is not None:
                self._publish_room_status(need_scheduling=True)
            else:
                # 停止条件已由 _should_stop_scheduling() 处理（切 IDLE + 发布）；
                # 若状态仍为 SCHEDULING，说明是 OPERATOR 等待情形，切换到 IDLE
                if self._state == RoomState.SCHEDULING:
                    self._state = RoomState.IDLE
                    self._publish_room_status()

        return changed

    def inject_runtime_state(
        self,
        messages: List[GtCoreRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
        turn_pos: int | None = None,
    ) -> None:
        if messages is not None:
            self.messages = list(messages)
        if agent_read_index is not None:
            converted: Dict[int, int] = {}
            for k, v in agent_read_index.items():
                try:
                    agent_id = int(k)
                    converted[agent_id] = v
                except (ValueError, TypeError):
                    pass  # 忽略无效的 key
            self._agent_read_index = converted
        if turn_pos is not None:
            self._turn_pos = turn_pos

    def export_agent_read_index(self) -> Dict[str, int]:
        """导出消息读取进度，key 为 agent 稳定标识名（用于持久化）。"""
        return {
            self._get_agent_stable_name(aid): idx
            for aid, idx in self._agent_read_index.items()
        }

    def mark_all_messages_read(self) -> None:
        tail = len(self.messages)
        self._agent_read_index = {agent_id: tail for agent_id in self._agent_ids}

    def rebuild_state_from_history(self, persisted_turn_pos: int | None = None) -> None:
        """从持久化数据重建房间调度数据（turn_pos 等），但不切换状态。

        状态始终保持 INIT，由 activate_scheduling() 统一决定目标状态。
        不逐条回放消息（回放会产生误判的"插话"日志且无法正确推进发言位）。

        Args:
            persisted_turn_pos: 从数据库恢复的发言位索引。
        """
        if not self._agent_ids or self._max_turns <= 0:
            return

        self._turn_count = 0
        if persisted_turn_pos is not None and 0 <= persisted_turn_pos < len(self._agent_ids):
            self._turn_pos = persisted_turn_pos
        else:
            self._turn_pos = 0
        self._round_skipped_set = set()
        self._current_turn_has_content = False

    def format_log(self) -> str:
        lines = [f"=== {self.key} 聊天记录 ==="]
        for msg in self.messages:
            sender_name = self._get_agent_name(msg.sender_id)
            lines.append(f"[{msg.send_time.isoformat()}] {sender_name}: {msg.content}")
        return "\n".join(lines)

    def _get_agent_display_name(self, agent: GtAgent) -> str:
        """获取 Agent 的显示名称（从 i18n 解析）。"""
        return i18nUtil.extract_i18n_str(
            agent.i18n.get("display_name") if agent.i18n else None,
            default=agent.name,
        ) or agent.name

    def build_initial_system_message(self) -> str:
        # 获取房间显示名称
        room_display_name = i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("display_name") if self.gt_room.i18n else None,
            default=self.name,
        ) or self.name

        # 获取所有 Agent 的显示名称（排除系统成员）
        agent_display_names = [
            self._get_agent_display_name(agent)
            for agent in self._agents
            if agent.id != self.SYSTEM_MEMBER_ID
        ]

        # 根据语言选择分隔符：中文用顿号，英文用逗号
        lang = configUtil.get_language()
        separator = "、" if lang == "zh-CN" else ", "

        agent_list_str = separator.join(agent_display_names)
        msg = i18nUtil.t("room_created_msg", room_name=room_display_name, agent_list=agent_list_str)
        if self.initial_topic:
            msg += f"\n{i18nUtil.t('room_initial_topic', topic=self.initial_topic)}"
        return msg

    def _build_current_turn_agent_dict(self) -> dict | None:
        """构建当前发言人信息字典，供 API 响应和事件广播复用。"""
        if self._state != RoomState.SCHEDULING or not self._agent_ids:
            return None
        agent_id = self._get_current_turn_agent_id()
        agent = self._get_agent_by_id(agent_id)
        return {
            "id": agent_id,
            "name": agent.name if agent else self._get_agent_stable_name(agent_id),
            "i18n": agent.i18n if agent is not None else {},
        }

    def to_dict(self) -> dict:
        """返回用于 API 响应的字典表示，包含 gt_room 详情与运行时状态。"""
        return {
            "gt_room": {
                "id": self.gt_room.id,
                "team_id": self.gt_room.team_id,
                "name": self.gt_room.name,
                "i18n": self.gt_room.i18n or {},
                "type": self.gt_room.type.name,
                "initial_topic": self.gt_room.initial_topic,
                "max_turns": self.gt_room.max_turns,
                "agent_ids": list(self.gt_room.agent_ids or []),
                "biz_id": self.gt_room.biz_id,
                "tags": list(self.gt_room.tags or []),
            },
            "team_name": self.team_name,
            "state": self._state.name,
            "need_scheduling": self._state == RoomState.SCHEDULING,
            "current_turn_agent": self._build_current_turn_agent_dict(),
            "agents": list(self.agents),
        }


_rooms: Dict[str, ChatRoom] = {}  # room_key -> ChatRoom
_rooms_by_id: Dict[int, ChatRoom] = {}


async def startup() -> None:
    """初始化房间服务，清空所有房间。"""
    _rooms.clear()
    _rooms_by_id.clear()


async def _load_room(
    gt_team: GtTeam,
    gt_room: GtRoom,
    agent_ids: List[int],
) -> None:
    """将数据库房间装载到运行态。"""
    room_agents = await gtAgentManager.get_team_agents_by_ids(gt_team.id, agent_ids, include_special=True)

    room = ChatRoom(team=gt_team, room=gt_room, agents=room_agents)
    _rooms[room.key] = room
    _rooms_by_id[room.room_id] = room

    logger.info(f"创建并初始化聊天室: room_id={room.room_id}, type={room.room_type.name}, agents={[agent.name for agent in room_agents]}")
    if gt_room.max_turns > 0:
        logger.info(f"初始化轮次配置: room_id={room.room_id}, max_turns={gt_room.max_turns}")


async def load_team_rooms(team_id: int) -> None:
    """从数据库读取指定 Team 的房间配置，并重建对应的内存房间对象。"""
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    if gt_team is None:
        logger.warning(f"加载 Team 房间失败: Team ID '{team_id}' 不存在")
        return

    await close_team_rooms(team_id)

    gt_rooms = await gtRoomManager.get_rooms_by_team(gt_team.id)
    for gt_room in gt_rooms:
        await _load_room(
            gt_team=gt_team,
            gt_room=gt_room,
            agent_ids=gt_room.agent_ids or [],
        )

    logger.info(f"Team '{gt_team.name}' 的内存房间已重建，共 {len(gt_rooms)} 个房间")


async def load_all_rooms() -> None:
    """从数据库读取所有房间配置，并创建对应的内存房间对象。"""
    for gt_team in await gtTeamManager.get_all_teams():
        for gt_room in await gtRoomManager.get_rooms_by_team(gt_team.id):
            await _load_room(
                gt_team=gt_team,
                gt_room=gt_room,
                agent_ids=gt_room.agent_ids or [],
            )


async def close_team_rooms(team_id: int) -> None:
    """关闭并移除指定 Team 的内存房间对象。"""
    to_close = [room_key for room_key, room in _rooms.items() if room.team_id == team_id]
    for room_key in to_close:
        room = _rooms.pop(room_key)
        _rooms_by_id.pop(room.room_id, None)
    logger.info(f"Team ID={team_id} 的 {len(to_close)} 个聊天室已关闭")


async def _restore_room_runtime_state(room: ChatRoom) -> None:
    """恢复单个房间的消息、已读指针和轮次进度。"""
    gt_room_messages = await gtRoomMessageManager.get_room_messages(room.room_id)
    agent_read_index, turn_pos = await gtRoomManager.get_room_state(room.room_id)
    recovered_from_db = bool(gt_room_messages)
    restored_messages: list[GtCoreRoomMessage] | None = None

    logger.info(f"[恢复状态] room={room.name}, room_id={room.room_id}, msg_count={len(gt_room_messages)}, read_index={agent_read_index}, turn_pos={turn_pos}")
    logger.info(f"[恢复状态-详细] room_id={room.room_id}, agent_read_index type={type(agent_read_index)}, turn_pos type={type(turn_pos)}")

    if gt_room_messages:
        restored_messages = []
        for row in gt_room_messages:
            restored_messages.append(GtCoreRoomMessage(
                sender_id=row.agent_id,
                sender_name=room._get_agent_name(row.agent_id),
                content=row.content,
                send_time=datetime.fromisoformat(row.send_time),
            ))

    if restored_messages is not None or agent_read_index is not None:
        room.inject_runtime_state(
            messages=restored_messages,
            agent_read_index=agent_read_index,
            turn_pos=turn_pos,
        )
    elif recovered_from_db and room.messages:
        room.mark_all_messages_read()

    room.rebuild_state_from_history(persisted_turn_pos=turn_pos if recovered_from_db else None)


async def restore_team_rooms_runtime_state(team_id: int) -> None:
    """恢复指定 Team 下所有内存房间的消息、已读指针和轮次进度。"""
    for room in get_all_rooms():
        if room.team_id != team_id:
            continue
        await _restore_room_runtime_state(room)


async def restore_all_rooms_runtime_state() -> None:
    """恢复所有内存房间的消息、已读指针和轮次进度。"""
    for room in get_all_rooms():
        await _restore_room_runtime_state(room)


def get_room_by_key(room_key: str) -> ChatRoom:
    """通过 room_key（room_name@team_name）返回聊天室实例。"""
    room = _rooms.get(room_key)
    if room is None:
        raise RuntimeError(f"聊天室 '{room_key}' 不存在")
    return room


def get_room(room_id: int) -> ChatRoom | None:
    """通过数据库主键 room_id 返回聊天室实例，不存在时返回 None。"""
    return _rooms_by_id.get(room_id)


async def get_room_messages_from_db(room_id: int) -> list[GtRoomMessage]:
    """从数据库加载房间消息，固定走持久层。"""
    return await gtRoomMessageManager.get_room_messages(room_id)


def get_all_rooms() -> List[ChatRoom]:
    """返回所有聊天室实例列表。"""
    return list(_rooms.values())


def shutdown() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()
    _rooms_by_id.clear()


async def update_room_agents(room_id: int, agent_ids: list[int]) -> None:
    room = await gtRoomManager.get_room_by_id(room_id)
    assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")

    room.agent_ids = agent_ids
    await gtRoomManager.save_room(room)


async def overwrite_dept_rooms(team_id: int, rooms: Sequence[DeptRoomSpec]) -> None:
    """按部门房间信息同步 DEPT 房间。

    行为约定：
    - 以 biz_id 作为幂等键，存在则更新，不存在则创建。
    - 每个目标房间都会同步 Agent 列表为 spec.agent_ids。
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
        # 从 i18n 解析 initial_topic，若无 i18n 则使用 spec.initial_topic
        if spec.i18n and "initial_topic" in spec.i18n:
            lang = configUtil.get_language()
            room.initial_topic = i18nUtil.extract_i18n_str(
                spec.i18n.get("initial_topic"),
                default=spec.initial_topic,
                lang=lang,
            ) or spec.initial_topic
        else:
            room.initial_topic = spec.initial_topic
        room.max_turns = resolve_room_max_turns(spec.max_turns)
        room.biz_id = spec.biz_id
        room.tags = ["DEPT"]
        room.i18n = spec.i18n or {}

        # 2) 保存房间元信息，再覆盖成员列表。
        saved_room = await gtRoomManager.save_room(room)
        await update_room_agents(saved_room.id, spec.agent_ids)

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


def get_agent_names(room_id: int) -> List[str]:
    """返回聊天室的参与者名列表。"""
    room = get_room(room_id)
    return room.agent_names if room is not None else []


def get_rooms_for_agent(team_id: int | None, agent_id: int) -> List[int]:
    """返回指定参与者所在的房间 room_id 列表。可选按 team 过滤。

    Args:
        team_id: Team ID，为 None 时不过滤
        agent_id: Agent ID
    """
    results = []
    for room in _rooms.values():
        if agent_id in room._agent_ids:
            if team_id is None or room.team_id == team_id:
                results.append(room.room_id)
    return results


async def activate_rooms(team_name: str | None = None) -> None:
    """统一激活入口：对目标房间调用 activate_scheduling（可按 team 过滤）。"""
    for room in _rooms.values():
        if team_name is not None and room.team_name != team_name:
            continue
        await room.activate_scheduling()
