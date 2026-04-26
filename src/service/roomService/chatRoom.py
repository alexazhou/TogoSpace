from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from dal.db import gtRoomManager, gtRoomMessageManager
from service import messageBus
from util import configUtil, i18nUtil
from util import assertUtil
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent
from .messageStore import RoomMessageStore

logger = logging.getLogger("service.roomService")


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    # 特殊 Agent ID
    SYSTEM_MEMBER_ID = int(SpecialAgent.SYSTEM.value)
    OPERATOR_MEMBER_ID = int(SpecialAgent.OPERATOR.value)

    def __init__(self, team: GtTeam, room: GtRoom, agents: List[GtAgent] | None = None):
        self.gt_room: GtRoom = room
        self.gt_team: GtTeam = team
        self._agents: List[GtAgent] = agents or []  # 房间参与者列表
        self._agent_ids: List[int] = [agent.id for agent in self._agents]  # agent_id 列表，调度逻辑频繁使用索引访问
        self._store = RoomMessageStore(self._agent_ids)  # 消息历史与已读进度
        self._turn_count: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.INIT  # 房间当前的调度状态
        self._round_skipped_set: set[int] = set()  # 当前轮次已跳过发言的 Agent ID 集合
        self._current_turn_has_content: bool = False  # 当前发言人是否已发送内容

    # ─── 从 gt_room / gt_team 派生的只读属性 ────────────────────

    @property
    def messages(self) -> List[GtCoreRoomMessage]:
        return self._store.messages

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

    def get_agent_ids(self, include_system: bool = False) -> List[int]:
        """返回 Agent ID 列表。

        Args:
            include_system: True 时包含 SYSTEM agent，默认 False。
        """
        if include_system:
            return [agent.id for agent in self._agents]
        return [agent.id for agent in self._agents if agent.id != self.SYSTEM_MEMBER_ID]

    def _get_gt_agent(self, agent_id: int) -> GtAgent:
        """根据 agent_id 获取运行态房间中的 Agent 对象。

        Assert 确保 agent_id 必在房间成员中。
        """
        for agent in self._agents:
            if agent.id == agent_id:
                return agent
        assert False, f"agent_id '{agent_id}' not found in room '{self.key}'"

    def _get_agent_stable_name(self, agent_id: int) -> str:
        """根据 agent_id 获取稳定标识名（用于持久化和匹配）。"""
        if agent_id == self.SYSTEM_MEMBER_ID:
            return "SYSTEM"
        return self._get_gt_agent(agent_id).name

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
        new_msgs = self._store.get_unread(agent_id)
        await self._persist_room_state()
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
        # SYSTEM 使用固定名称，其他从 _agents 获取 display_name
        if sender_id == self.SYSTEM_MEMBER_ID:
            sender_display_name = "系统提醒"
        else:
            sender_display_name = self._get_gt_agent(sender_id).display_name

        message = GtCoreRoomMessage(
            sender_id=sender_id,
            sender_display_name=sender_display_name,
            content=content,
            send_time=send_time or datetime.now()
        )
        self._store.append(message)

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
            logger.info(f"检测到房间 {self.key} 的活动 ({self._get_agent_stable_name(sender_id)}(agent_id={sender_id}))，重置轮次计数器并唤醒房间")
            self._turn_count = 0
            self._round_skipped_set = set()
            self._current_turn_has_content = False
            self._state = RoomState.SCHEDULING

        # 2. 只有当前顺序发言人说话，才标记本轮有内容。不再自动推进
        current_expected = self._get_current_turn_agent_id()
        if sender_id == current_expected:
            self._current_turn_has_content = True
        else:
            logger.info(f"房间 {self.key} 收到来自 {self._get_agent_stable_name(sender_id)}(agent_id={sender_id}) 的插话，保持当前发言位 (当前应轮到 {self._get_agent_stable_name(current_expected)}(agent_id={current_expected}))")

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
            logger.warning(f"房间 {self.key} 拒绝结束轮次申请：{self._get_agent_stable_name(agent_id)}(agent_id={agent_id}) 并非当前发言人 {self._get_agent_stable_name(current_expected)}(agent_id={current_expected})")
            return False

        logger.info(
            "房间 %s 由 %s(agent_id=%d) 结束本轮行动 (has_content=%s, turn_pos=%d/%d, turn_count=%d)",
            self.key, self._get_agent_stable_name(current_expected), current_expected,
            self._current_turn_has_content, self._turn_pos, len(self._agent_ids), self._turn_count,
        )

        # 如果本轮没说话，记录为跳过
        if not self._current_turn_has_content:
            self._round_skipped_set.add(current_expected)

        self._current_turn_has_content = False

        if not self._agent_ids:
            return True

        self._go_next_turn()
        await self._persist_room_state()
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

    async def _persist_room_state(self) -> None:
        """持久化当前 turn_pos 与各 Agent 已读进度。"""
        if self._state == RoomState.INIT:
            return
        id_keyed = {str(k): v for k, v in self._store.get_read_index().items()}
        await gtRoomManager.update_room_state(self.room_id, id_keyed, self._turn_pos)

    def get_current_turn_agent(self) -> GtAgent:
        """返回当前理论上应该发言的 GtAgent 对象（忽略 IDLE 状态）。"""
        return self._get_gt_agent(self._get_current_turn_agent_id())

    def get_current_turn_agent_id(self) -> int:
        """返回当前理论上应该发言的 Agent ID（忽略 IDLE 状态）。"""
        return self._get_current_turn_agent_id()

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
            self._get_gt_agent(self._get_current_turn_agent_id())
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
                logger.info(f"房间 {self.key} 自动跳过人类操作者回合: {self._get_agent_stable_name(next_id)}(agent_id={next_id})")
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
                    self._get_agent_stable_name(next_id),
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
        self._store.inject(messages=messages, agent_read_index=agent_read_index)
        if turn_pos is not None:
            self._turn_pos = turn_pos

    def export_agent_read_index(self) -> Dict[str, int]:
        """导出消息读取进度，key 为 agent 稳定标识名（用于持久化）。"""
        return {
            self._get_agent_stable_name(aid): idx
            for aid, idx in self._store.get_read_index().items()
        }

    def mark_all_messages_read(self) -> None:
        self._store.mark_all_read()

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
            sender_name = self._get_agent_stable_name(msg.sender_id)
            lines.append(f"[{msg.send_time.isoformat()}] {sender_name}: {msg.content}")
        return "\n".join(lines)

    def _get_room_initial_topic_display_text(self) -> str:
        """按当前后端语言解析首条系统消息里展示的 initial topic。"""
        return i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("initial_topic") if self.gt_room.i18n else None,
            default=self.initial_topic,
        ) or self.initial_topic

    def build_initial_system_message(self) -> str:
        # 获取房间显示名称
        room_display_name = i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("display_name") if self.gt_room.i18n else None,
            default=self.name,
        ) or self.name

        # 获取所有 Agent 的显示名称（排除系统成员）
        agent_display_names = [
            agent.display_name
            for agent in self._agents
            if agent.id != self.SYSTEM_MEMBER_ID
        ]

        # 根据语言选择分隔符：中文用顿号，英文用逗号
        lang = configUtil.get_language()
        separator = "、" if lang == "zh-CN" else ", "

        agent_list_str = separator.join(agent_display_names)
        msg = i18nUtil.t("room_created_msg", room_name=room_display_name, agent_list=agent_list_str)
        initial_topic_text = self._get_room_initial_topic_display_text()
        if initial_topic_text:
            msg += f"\n{i18nUtil.t('room_initial_topic', topic=initial_topic_text)}"
        return msg

    def _build_current_turn_agent_dict(self) -> dict | None:
        """构建当前发言人信息字典，供 API 响应和事件广播复用。"""
        if self._state != RoomState.SCHEDULING or not self._agent_ids:
            return None
        agent_id = self._get_current_turn_agent_id()
        agent = self._get_gt_agent(agent_id)
        return {
            "id": agent_id,
            "i18n": agent.i18n,
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
            "agents": list(self.get_agent_ids()),
        }


