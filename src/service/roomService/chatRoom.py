from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from dal.db import gtRoomManager, gtRoomMessageManager, gtAgentManager
from service import messageBus
from util import configUtil, i18nUtil
from util import assertUtil
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoom import GtRoom
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent
from .messageStore import RoomMessageStore

logger = logging.getLogger("service.roomService")


class ChatRoom:
    """聊天室数据类（内部实现，外部通过模块级函数访问）"""

    # 特殊 Agent ID
    SYSTEM_MEMBER_ID = int(SpecialAgent.SYSTEM.value)
    OPERATOR_MEMBER_ID = int(SpecialAgent.OPERATOR.value)

    def __init__(self, team: GtTeam, room: GtRoom, agent_ids: List[int] | None = None):
        self.gt_room: GtRoom = room
        self.gt_team: GtTeam = team
        self._agent_ids: List[int] = agent_ids or []  # agent_id 列表，调度逻辑频繁使用索引访问
        self._store = RoomMessageStore(self._agent_ids)  # 消息历史与已读进度
        self._turn_count: int = 0  # 轮次计数器（完成一圈全员发言记为 1 轮）
        self._turn_pos: int = 0  # 当前轮次在参与者列表中的位置索引
        self._state: RoomState = RoomState.INIT  # 房间当前的调度状态
        self._round_skipped_set: set[int] = set()  # 当前轮次已跳过发言的 Agent ID 集合
        self.current_turn_has_content: bool = False  # 当前发言人是否已发送内容

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
            return list(self._agent_ids)
        return [aid for aid in self._agent_ids if aid != self.SYSTEM_MEMBER_ID]

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

    def has_pending_immediate_messages(self, agent_id: int) -> bool:
        """检查是否存在 insert_immediately=True 且 agent 尚未读取的消息。"""
        return self._store.has_pending_immediate_messages(agent_id)

    async def add_message(self, sender_id: int, content: str, send_time: datetime | None = None, *, insert_immediately: bool = False) -> None:
        """添加消息到房间。"""
        await self._append_message(sender_id, content, send_time=send_time, insert_immediately=insert_immediately)

    async def _append_message(
        self,
        sender_id: int,
        content: str,
        send_time: datetime | None = None,
        *,
        update_turn_state: bool = True,
        insert_immediately: bool = False,
    ) -> None:
        assertUtil.assertTrue(
            self.can_post_message(sender_id),
            error_message=f"sender_id '{sender_id}' is not an agent of room '{self.key}'",
            error_code="sender_not_in_room",
        )
        # insert_immediately 仅限私聊房间
        if insert_immediately and self.room_type != RoomType.PRIVATE:
            logger.warning("房间 %s 非私聊房间，immediately 标志不支持，降级为普通消息", self.key)
            insert_immediately = False
        # 若房间当前不在调度中，immediately 标志无意义，降级为普通消息
        if insert_immediately and self._state != RoomState.SCHEDULING:
            logger.warning("房间 %s 非 SCHEDULING 状态，immediately 消息降级为普通消息", self.key)
            insert_immediately = False

        # 私聊房间调度中时，OPERATOR 发送的普通消息延迟到本轮结束后注入（queued）
        is_queued = (
            not insert_immediately
            and sender_id == self.OPERATOR_MEMBER_ID
            and self.room_type == RoomType.PRIVATE
            and self._state == RoomState.SCHEDULING
        )

        # 从数据库获取 display_name（SYSTEM agent 也有数据库记录）
        agent = await gtAgentManager.get_agent_by_id(sender_id)
        assert agent, f"agent_id '{sender_id}' not found"
        sender_display_name = agent.display_name

        message = GtCoreRoomMessage(
            sender_id=sender_id,
            sender_display_name=sender_display_name,
            content=content,
            send_time=send_time or datetime.now(),
            insert_immediately=insert_immediately,
        )

        if insert_immediately or is_queued:
            # immediately/queued 消息进入等待注入队列，seq 尚未分配，由注入时赋值
            self._store.append_pending(message)
        else:
            self._store.append_and_assign_seq(message)

        if self._state == RoomState.INIT:
            return

        db_msg = await gtRoomMessageManager.append_room_message(
            room_id=self.room_id,
            agent_id=sender_id,
            content=content,
            send_time=message.send_time.isoformat(),
            insert_immediately=insert_immediately,
            seq=message.seq,  # immediately/queued 消息此时为 None
        )
        message.db_id = db_msg.id

        messageBus.publish(
            MessageBusTopic.ROOM_MSG_ADDED,
            gt_room=self.gt_room,
            gt_message=message,
        )

        if not insert_immediately and not is_queued:
            if update_turn_state and self._agent_ids:
                self._update_turn_state_on_message(sender_id)

    async def flush_pending_immediate_messages(self) -> None:
        """将待注入队列中的 immediately 消息移入主消息列表，分配 seq，更新 DB，广播 WS。

        由 agentTurnRunner 在安全边界调用。
        """
        flushed = self._store.flush_pending_immediate()
        if not flushed:
            return
        for msg in flushed:
            if msg.db_id is not None:
                await gtRoomMessageManager.update_room_message_seq(msg.db_id, msg.seq)  # type: ignore[arg-type]
            messageBus.publish(
                MessageBusTopic.ROOM_MSG_CHANGED,
                gt_room=self.gt_room,
                gt_message=msg,
            )
        logger.info(
            "immediately 消息注入完成: room=%s, count=%d, seqs=%s",
            self.key, len(flushed), [m.seq for m in flushed],
        )

    async def flush_queued_messages(self) -> None:
        """将 queued 消息（OPERATOR 在调度中发送的普通消息）分配 seq，更新 DB，广播 WS，并推进 OPERATOR 轮次。

        queued 消息的 seq 在当前 Agent 轮次结束后才分配，因此在时间线上出现在 Agent 回复之后。
        由 agentTurnRunner 在 Agent 轮次结束后（_run_turn_loop 返回后）调用。
        若存在 queued 消息，flush 后以 OPERATOR 身份完成本轮，触发下一位 AI 调度。
        """
        flushed = self._store.flush_queued()
        if not flushed:
            return
        for msg in flushed:
            if msg.db_id is not None:
                await gtRoomMessageManager.update_room_message_seq(msg.db_id, msg.seq)  # type: ignore[arg-type]
            messageBus.publish(
                MessageBusTopic.ROOM_MSG_CHANGED,
                gt_room=self.gt_room,
                gt_message=msg,
            )
            if self._agent_ids:
                self._update_turn_state_on_message(msg.sender_id)
        await self.finish_turn(self.OPERATOR_MEMBER_ID)
        logger.info(
            "queued 消息 flush 完成: room=%s, count=%d, seqs=%s",
            self.key, len(flushed), [m.seq for m in flushed],
        )

    async def escalate_message_to_immediate(self, db_id: int) -> None:
        """将主消息列表中尚未被 agent 读取的消息升级为 immediately 消息。

        消息会从主列表移入 pending 队列，并更新 DB 中的 seq 和 insert_immediately 字段。
        若消息不存在或已被 agent 读取，抛出异常。
        """
        msg = self._store.escalate_to_immediate(db_id)
        await gtRoomMessageManager.escalate_message_to_immediate(db_id)
        messageBus.publish(
            MessageBusTopic.ROOM_MSG_CHANGED,
            gt_room=self.gt_room,
            gt_message=msg,
        )
        logger.info("消息升级为 immediately: room=%s, db_id=%d", self.key, db_id)

    def _update_turn_state_on_message(self, sender_id: int) -> None:
        # 1. 唤醒检查：IDLE 或 INIT 状态下收到消息，自动进入调度
        was_idle = self._state in (RoomState.IDLE, RoomState.INIT)
        if was_idle:
            logger.info(f"检测到房间 {self.key} 的活动 (agent={gtAgentManager.get_agent_name(sender_id)})，重置轮次计数器并唤醒房间")
            self._turn_count = 0
            self._round_skipped_set = set()
            self.current_turn_has_content = False
            self._state = RoomState.SCHEDULING

        # 2. 只有当前顺序发言人说话，才标记本轮有内容。不再自动推进
        current_expected = self._get_current_turn_agent_id()
        if sender_id == current_expected:
            self.current_turn_has_content = True
        else:
            logger.info(f"房间 {self.key} 收到来自 agent={gtAgentManager.get_agent_name(sender_id)} 的插话，保持当前发言位 (当前应轮到 agent={gtAgentManager.get_agent_name(current_expected)})")

        # 3. 只要有真实消息（非系统消息），就清空跳过记录，让所有人重新有机会回应
        if sender_id != self.SYSTEM_MEMBER_ID and self._round_skipped_set:
            self._round_skipped_set = set()

        # 4. 如果刚才从 IDLE 唤醒，重新发布调度事件
        if was_idle:
            if self._is_stop_condition_met():
                self._transition_to_idle_on_stop()
                return
            next_agent_id = self._advance_to_next_dispatchable()
            if next_agent_id is not None:
                self._publish_room_status(need_scheduling=True)
            else:
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
            logger.warning(f"房间 {self.key} 拒绝结束轮次申请：agent={gtAgentManager.get_agent_name(agent_id)} 并非当前发言人 agent={gtAgentManager.get_agent_name(current_expected)}")
            return False

        logger.info(
            "房间 %s 由 agent=%s 结束本轮行动 (has_content=%s, turn_pos=%d/%d, turn_count=%d)",
            self.key, gtAgentManager.get_agent_name(current_expected),
            self.current_turn_has_content, self._turn_pos, len(self._agent_ids), self._turn_count,
        )

        # 如果本轮没说话，记录为跳过
        if not self.current_turn_has_content:
            self._round_skipped_set.add(current_expected)

        self.current_turn_has_content = False

        if not self._agent_ids:
            return True

        self._go_next_turn()
        await self._persist_room_state()
        if self._is_stop_condition_met():
            self._transition_to_idle_on_stop()
            return True
        next_agent_id = self._advance_to_next_dispatchable()
        if next_agent_id is not None:
            self._publish_room_status(need_scheduling=True)
        elif self._state == RoomState.SCHEDULING:
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
        current_turn_agent_id = (
            self._get_current_turn_agent_id()
            if self._state == RoomState.SCHEDULING and self._agent_ids
            else None
        )
        messageBus.publish(
            MessageBusTopic.ROOM_STATUS_CHANGED,
            gt_room=self.gt_room,
            state=self._state,
            current_turn_agent_id=current_turn_agent_id,
            need_scheduling=need_scheduling,
        )

    def cancel_current_turn(self) -> None:
        """人工停止当前 turn 后，将房间切回 IDLE，等待后续新消息重新唤醒。"""
        if self._state != RoomState.SCHEDULING:
            return

        self.current_turn_has_content = False
        self._state = RoomState.IDLE
        logger.info("房间 %s 当前 turn 被人工停止，切回 IDLE 等待新消息唤醒", self.key)
        self._publish_room_status()

    def _silently_skip(self, agent_id: int) -> None:
        """跳过当前发言人：标记跳过、清除内容、推进发言位。"""
        self._round_skipped_set.add(agent_id)
        self.current_turn_has_content = False
        self._go_next_turn()

    def _advance_to_next_dispatchable(self) -> Optional[int]:
        """从当前发言位出发，向前推进直到遇到可调度的 AI Agent。
        GROUP 中自动跳过 OPERATOR（成员数 > 2）；遇到 SpecialAgent 等待输入。
        返回 None 表示当前不应发布调度事件。"""
        if not self._agent_ids:
            return None

        while True:
            agent_id = self._get_current_turn_agent_id()

            if self._should_auto_skip_agent_turn():
                self._silently_skip(agent_id)
                if self._is_stop_condition_met():
                    self._transition_to_idle_on_stop()
                    return None
                continue

            if self._is_special_agent(agent_id):
                return None

            return agent_id

    def _is_stop_condition_met(self) -> bool:
        """判断是否满足停止调度的条件（纯查询，无副作用）。

        满足以下任一条件则返回 True：
        - max_turns > 0 且 turn_count >= max_turns（已达到最大轮次；<=0 = 不限轮次）
        - 所有 AI 成员均在本轮中跳过发言
        """
        if self._max_turns > 0 and self._turn_count >= self._max_turns:
            return True
        ai_agent_ids = {aid for aid in self._agent_ids if aid != self.OPERATOR_MEMBER_ID}
        return bool(ai_agent_ids) and ai_agent_ids.issubset(self._round_skipped_set)

    def _transition_to_idle_on_stop(self) -> None:
        """在满足停止条件后将房间切换到 IDLE 状态并广播（应在 _is_stop_condition_met 返回 True 后调用）。"""
        if self._state == RoomState.IDLE:
            return
        self._state = RoomState.IDLE
        if self._max_turns > 0 and self._turn_count >= self._max_turns:
            logger.info(f"房间 {self.key} 已达到最大轮次 {self._max_turns}，进入 IDLE 状态")
        else:
            logger.info(f"房间 {self.key} 所有 AI 成员均已跳过发言（自上次消息以来），停止调度")
        self._publish_room_status()

    def _go_next_turn(self) -> None:
        """推进到下一发言位。"""
        self._turn_pos = (self._turn_pos + 1) % len(self._agent_ids)

        # turn_pos 回到 0 代表跨轮（从最后一位回到首位）；
        # 只有在跨轮时才推进 turn_count。
        if self._turn_pos == 0:
            self._turn_count += 1

    async def activate_scheduling(self) -> bool:
        """将房间从 INIT 状态激活。

        - 插入初始系统消息（若无消息）
        - 若有可立即调度的 agent，进入 SCHEDULING 并触发首次调度
        - 否则进入 IDLE，等待后续消息唤醒

        返回是否发生了 INIT -> 非 INIT 的状态切换。
        """
        if self._state != RoomState.INIT:
            return False

        # 先离开 INIT，否则 _append_message 的 DB 写入会被跳过
        self._state = RoomState.IDLE

        if not self.messages:
            await self._append_message(self.SYSTEM_MEMBER_ID, await self.build_initial_system_message(), update_turn_state=False)

        next_agent_id = self._advance_to_next_dispatchable()

        if next_agent_id is not None:
            self._state = RoomState.SCHEDULING
            self._publish_room_status(need_scheduling=True)
        else:
            self._publish_room_status()

        logger.info(f"[{self.key}] 房间激活: INIT -> {self._state.name} (agents={len(self._agent_ids)}, max_turns={self._max_turns})")
        return True

    def inject_runtime_state(
        self,
        messages: List[GtCoreRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
        turn_pos: int | None = None,
    ) -> None:
        self._store.inject(messages=messages, agent_read_index=agent_read_index)
        if turn_pos is not None:
            self._turn_pos = turn_pos

    def export_agent_read_index(self) -> Dict[int, int]:
        """导出消息读取进度，key 为 agent_id（用于持久化）。"""
        return dict(self._store.get_read_index().items())

    def mark_all_messages_read(self) -> None:
        self._store.mark_all_read()

    def rebuild_state_from_history(self, persisted_turn_pos: int | None = None) -> None:
        """从持久化数据重建房间调度数据（turn_pos 等），但不切换状态。

        状态始终保持 INIT，由 activate_scheduling() 统一决定目标状态。
        不逐条回放消息（回放会产生误判的"插话"日志且无法正确推进发言位）。

        Args:
            persisted_turn_pos: 从数据库恢复的发言位索引。
        """
        if not self._agent_ids:
            return

        self._turn_count = 0
        if persisted_turn_pos is not None and 0 <= persisted_turn_pos < len(self._agent_ids):
            self._turn_pos = persisted_turn_pos
        else:
            self._turn_pos = 0
        self._round_skipped_set = set()
        self.current_turn_has_content = False

    def format_log(self) -> str:
        lines = [f"=== {self.key} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.send_time.isoformat()}] {msg.sender_display_name}: {msg.content}")
        return "\n".join(lines)

    def _get_room_initial_topic_display_text(self) -> str:
        """按当前后端语言解析首条系统消息里展示的 initial topic。"""
        return i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("initial_topic") if self.gt_room.i18n else None,
            default=self.initial_topic,
        ) or self.initial_topic

    async def build_initial_system_message(self) -> str:
        # 获取房间显示名称
        room_display_name = i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("display_name") if self.gt_room.i18n else None,
            default=self.name,
        ) or self.name

        # 从数据库获取所有 Agent 的显示名称（排除系统成员）
        agent_ids = [aid for aid in self._agent_ids if aid != self.SYSTEM_MEMBER_ID]
        agents = await gtAgentManager.get_agents_by_ids(agent_ids)
        agent_display_names = [agent.display_name for agent in agents]

        # 根据语言选择分隔符：中文用顿号，英文用逗号
        lang = configUtil.get_language()
        separator = "、" if lang == "zh-CN" else ", "

        agent_list_str = separator.join(agent_display_names)
        msg = i18nUtil.t("room_created_msg", room_name=room_display_name, agent_list=agent_list_str)
        initial_topic_text = self._get_room_initial_topic_display_text()
        if initial_topic_text:
            msg += f"\n{i18nUtil.t('room_initial_topic', topic=initial_topic_text)}"
        return msg

    def _build_current_turn_agent_id(self) -> int | None:
        """构建当前发言人 ID，供 API 响应复用。"""
        if self._state != RoomState.SCHEDULING or not self._agent_ids:
            return None
        return self._get_current_turn_agent_id()

    def to_dict(self) -> dict:
        """返回用于 API 响应的字典表示，包含 gt_room 详情与运行时状态。"""
        return {
            "gt_room": self.gt_room.to_json(),
            "team_name": self.team_name,
            "state": self._state.name,
            "need_scheduling": self._state == RoomState.SCHEDULING,
            "current_turn_agent_id": self._build_current_turn_agent_id(),
            "agents": list(self.get_agent_ids()),
        }
