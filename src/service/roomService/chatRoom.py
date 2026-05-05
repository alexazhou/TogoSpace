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
from constants import RoomState, MessageBusTopic, RoomType, SpecialAgent
from .messageStore import RoomMessageStore
from .roomScheduler import RoomScheduler

logger = logging.getLogger("service.roomService")


class ChatRoom:
    """聊天室 — facade 角色。

    消息存储委托给 RoomMessageStore，调度逻辑委托给 RoomScheduler。
    """

    SYSTEM_MEMBER_ID = int(SpecialAgent.SYSTEM.value)
    OPERATOR_MEMBER_ID = int(SpecialAgent.OPERATOR.value)

    def __init__(self, team: GtTeam, room: GtRoom):
        self.gt_room: GtRoom = room
        self.gt_team: GtTeam = team
        self._store = RoomMessageStore(room.agent_ids, gt_room=room)
        self._scheduler = RoomScheduler(
            agent_ids=room.agent_ids,
            room_key=self.key,
            gt_room=room,
            get_read_index=self._store.get_read_index,
        )

    # ─── 属性 delegation ─────────────────────────────────────

    @property
    def state(self) -> RoomState:
        return self._scheduler.state

    @property
    def _state(self) -> RoomState:
        return self._scheduler.state

    @property
    def _turn_pos(self) -> int:
        return self._scheduler.turn_pos

    @property
    def _turn_count(self) -> int:
        return self._scheduler._turn_count

    @property
    def _round_skipped_set(self) -> set[int]:
        return self._scheduler._round_skipped_set

    @property
    def current_turn_has_content(self) -> bool:
        return self._scheduler.current_turn_has_content

    @current_turn_has_content.setter
    def current_turn_has_content(self, value: bool) -> None:
        self._scheduler.current_turn_has_content = value

    def _get_current_turn_agent_id(self) -> int:
        return self._scheduler.get_current_turn_agent_id()

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
    def _max_turns(self) -> int:
        return self.gt_room.max_turns

    @property
    def initial_topic(self) -> str:
        return self.gt_room.initial_topic

    @property
    def tags(self) -> List[str]:
        return self.gt_room.tags or []

    @property
    def key(self) -> str:
        return f"{self.name}@{self.team_name}"

    @property
    def _agent_ids(self) -> List[int]:
        return self.gt_room.agent_ids

    def get_agent_ids(self, include_system: bool = False) -> List[int]:
        if include_system:
            return list(self._agent_ids)
        return [aid for aid in self._agent_ids if aid != self.SYSTEM_MEMBER_ID]

    def can_post_message(self, sender_id: int) -> bool:
        return sender_id in self._agent_ids or sender_id == self.SYSTEM_MEMBER_ID

    # ─── 调度 delegation ─────────────────────────────────────

    def get_current_turn_agent_id(self) -> int:
        return self._scheduler.get_current_turn_agent_id()

    async def finish_turn(self, agent_id: int) -> bool:
        assertUtil.assertNotNull(agent_id, error_message=f"agent_id 不能为空, room={self.key}")
        return await self._scheduler.finish_turn(agent_id)

    def cancel_current_turn(self) -> None:
        self._scheduler.cancel_current_turn()

    async def activate_scheduling(self) -> bool:
        if self._scheduler.state != RoomState.INIT:
            return False

        # 先离开 INIT，否则 _append_message 的 DB 写入会被跳过
        self._scheduler._set_state(RoomState.IDLE)

        if not self.messages:
            await self._append_message(self.SYSTEM_MEMBER_ID, await self.build_initial_system_message(), update_turn_state=False)

        self._scheduler.complete_activation()
        logger.info("[%s] 房间激活: INIT -> %s (agents=%d, max_turns=%d)",
                     self.key, self._scheduler.state.name, len(self._agent_ids), self.gt_room.max_turns)
        return True

    # ─── 消息 ─────────────────────────────────────────────────

    async def get_unread_messages(self, agent_id: int) -> List[GtCoreRoomMessage]:
        new_msgs = self._store.get_unread(agent_id)
        if self._scheduler.state != RoomState.INIT:
            await self._scheduler.persist_state()
        return new_msgs

    def has_pending_immediate_messages(self, agent_id: int) -> bool:
        return self._store.has_pending_immediate_messages(agent_id)

    async def add_message(self, sender_id: int, content: str, send_time: datetime | None = None, *,
                          insert_immediately: bool = False) -> None:
        await self._append_message(sender_id, content, send_time=send_time, insert_immediately=insert_immediately)

    async def _append_message(
        self, sender_id: int, content: str,
        send_time: datetime | None = None, *,
        update_turn_state: bool = True,
        insert_immediately: bool = False,
    ) -> None:
        assertUtil.assertTrue(
            self.can_post_message(sender_id),
            error_message=f"sender_id '{sender_id}' is not an agent of room '{self.key}'",
            error_code="sender_not_in_room",
        )
        state = self._scheduler.state

        # insert_immediately 仅限私聊 + 调度中
        if insert_immediately and (self.room_type != RoomType.PRIVATE or state != RoomState.SCHEDULING):
            logger.warning("房间 %s 不支持 immediately 消息 (room_type=%s, state=%s)，降级为普通消息",
                           self.key, self.room_type, state)
            insert_immediately = False

        # 私聊调度中 OPERATOR 普通消息 → queued
        is_queued = (
            not insert_immediately
            and sender_id == self.OPERATOR_MEMBER_ID
            and self.room_type == RoomType.PRIVATE
            and state == RoomState.SCHEDULING
        )

        agent = await gtAgentManager.get_agent_by_id(sender_id)
        assert agent, f"agent_id '{sender_id}' not found"

        message = GtCoreRoomMessage(
            sender_id=sender_id,
            sender_display_name=agent.display_name,
            content=content,
            send_time=send_time or datetime.now(),
            insert_immediately=insert_immediately,
        )

        if insert_immediately or is_queued:
            self._store.append_pending(message)
        else:
            self._store.append_and_assign_seq(message)

        if state == RoomState.INIT:
            return

        db_msg = await gtRoomMessageManager.append_room_message(
            room_id=self.room_id, agent_id=sender_id, content=content,
            send_time=message.send_time.isoformat(),
            insert_immediately=insert_immediately, seq=message.seq,
        )
        message.db_id = db_msg.id

        messageBus.publish(MessageBusTopic.ROOM_MSG_ADDED, gt_room=self.gt_room, gt_message=message)

        if not insert_immediately and not is_queued and update_turn_state and self._agent_ids:
            self._on_regular_message(sender_id)

    def _on_regular_message(self, sender_id: int) -> None:
        """常规消息（非 queued/not-immediate）触发调度状态更新。"""
        # 1. 唤醒
        wake_result = self._scheduler.wake_up(sender_id)

        # 2. 标记内容
        self._scheduler.mark_turn_content(sender_id)

        # 3. 清除跳过记录
        self._scheduler.clear_skip_on_real_message(sender_id)

        # 4. 唤醒后发布调度事件（非唤醒路径不发布）
        if wake_result is not None:
            if self._scheduler.should_stay_idle_after_wake():
                self._scheduler.publish_status()
            else:
                self._scheduler.publish_status(wake_result, need_scheduling=True)

    async def flush_pending_immediate_messages(self) -> None:
        await self._store.flush_pending_immediate()

    async def flush_queued_messages(self) -> None:
        flushed = await self._store.flush_queued()
        for msg in flushed:
            if self._agent_ids:
                self._scheduler.mark_turn_content(msg.sender_id)
        if flushed:
            await self.finish_turn(self.OPERATOR_MEMBER_ID)

    async def escalate_message_to_immediate(self, db_id: int) -> None:
        msg = self._store.escalate_to_immediate(db_id)
        await gtRoomMessageManager.escalate_message_to_immediate(db_id)
        messageBus.publish(MessageBusTopic.ROOM_MSG_CHANGED, gt_room=self.gt_room, gt_message=msg)
        logger.info("消息升级为 immediately: room=%s, db_id=%d", self.key, db_id)

    # ─── 运行时状态注入与导出 ─────────────────────────────────

    def inject_runtime_state(
        self,
        messages: List[GtCoreRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
        turn_pos: int | None = None,
    ) -> None:
        self._store.inject(messages=messages, agent_read_index=agent_read_index)
        if turn_pos is not None:
            self._scheduler._turn_pos = turn_pos

    def export_agent_read_index(self) -> Dict[int, int]:
        return dict(self._store.get_read_index().items())

    def mark_all_messages_read(self) -> None:
        self._store.mark_all_read()

    def rebuild_state_from_history(self, persisted_turn_pos: int | None = None) -> None:
        if not self._agent_ids:
            return
        self._scheduler.set_turn_pos(persisted_turn_pos)

    # ─── 辅助 ─────────────────────────────────────────────────

    def format_log(self) -> str:
        lines = [f"=== {self.key} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.send_time.isoformat()}] {msg.sender_display_name}: {msg.content}")
        return "\n".join(lines)

    async def build_initial_system_message(self) -> str:
        room_display_name = i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("display_name") if self.gt_room.i18n else None,
            default=self.name,
        ) or self.name

        agent_ids = [aid for aid in self._agent_ids if aid != self.SYSTEM_MEMBER_ID]
        agents = await gtAgentManager.get_agents_by_ids(agent_ids)
        agent_display_names = [agent.display_name for agent in agents]

        lang = configUtil.get_language()
        separator = "、" if lang == "zh-CN" else ", "
        agent_list_str = separator.join(agent_display_names)
        msg = i18nUtil.t("room_created_msg", room_name=room_display_name, agent_list=agent_list_str)
        initial_topic_text = self._get_room_initial_topic_display_text()
        if initial_topic_text:
            msg += f"\n{i18nUtil.t('room_initial_topic', topic=initial_topic_text)}"
        return msg

    def _get_room_initial_topic_display_text(self) -> str:
        return i18nUtil.extract_i18n_str(
            self.gt_room.i18n.get("initial_topic") if self.gt_room.i18n else None,
            default=self.initial_topic,
        ) or self.initial_topic

    def _build_current_turn_agent_id(self) -> int | None:
        if self._scheduler.state != RoomState.SCHEDULING or not self._agent_ids:
            return None
        return self._scheduler.get_current_turn_agent_id()

    def to_dict(self) -> dict:
        return {
            "gt_room": self.gt_room.to_json(),
            "team_name": self.team_name,
            "state": self._scheduler.state.name,
            "need_scheduling": self._scheduler.state == RoomState.SCHEDULING,
            "current_turn_agent_id": self._build_current_turn_agent_id(),
            "agents": list(self.get_agent_ids()),
        }
