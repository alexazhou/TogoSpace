from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from dal.db import gtRoomManager, gtRoomMessageManager, gtAgentManager
from service import messageBus
from util import configUtil, i18nUtil
from util import assertUtil
from model.dbModel.gtRoomMessage import GtRoomMessage
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
        # 内存运行时态（消息/读游标/_next_seq）是否已与 DB 对齐过。
        # 「读了但 DB 是空的」也算就绪；不能用 `if not self.messages` 代替。
        self._runtime_state_ready: bool = False
        self._store = RoomMessageStore(gt_room=room)
        self._scheduler = RoomScheduler(
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
    def _current_speaker_index(self) -> int | None:
        return self._scheduler.current_speaker_index

    @property
    def _round_count(self) -> int:
        return self._scheduler._round_count

    @property
    def _round_skipped_set(self) -> set[int]:
        return self._scheduler._current_round_skipped_set

    @property
    def current_turn_has_content(self) -> bool:
        return self._scheduler.current_turn_has_content

    @current_turn_has_content.setter
    def current_turn_has_content(self, value: bool) -> None:
        self._scheduler.current_turn_has_content = value

    @property
    def messages(self) -> List[GtRoomMessage]:
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
    def _max_rounds(self) -> int:
        if self.gt_room.max_rounds is not None:
            return self.gt_room.max_rounds
        return configUtil.get_app_config().setting.default_room_max_rounds

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

    async def handle_finish_request(self, caller_agent_id: int) -> bool:
        assertUtil.assertNotNull(caller_agent_id, error_message=f"agent_id 不能为空, room={self.key}")
        return await self._scheduler.handle_finish_request(caller_agent_id)

    def cancel_current_turn(self) -> None:
        self._scheduler.cancel_current_turn()

    async def activate_scheduling(self) -> bool:
        if self._scheduler.state != RoomState.INIT:
            return False

        # 未装载就写初始消息，会把有历史的房间当新房间补写 seq=0（线上 bug 根因）
        assertUtil.assertTrue(
            self._runtime_state_ready,
            error_message=f"房间运行时状态未装载，拒绝 activate_scheduling: {self.key}",
            error_code="room_state_not_ready",
        )

        if not self.messages:
            content = await self.build_initial_system_message()
            agent = await gtAgentManager.get_agent_by_id(self.SYSTEM_MEMBER_ID)
            msg = GtRoomMessage(room_id=self.room_id, sender_id=self.SYSTEM_MEMBER_ID,
                                content=content, send_time=datetime.now())
            msg.sender_display_name = agent.display_name
            await self._store.append_and_assign_seq(msg, publish=True)

        self._scheduler.activate()
        logger.info("[%s] 房间激活: INIT -> %s (agents=%d, max_rounds=%d)",
                     self.key, self._scheduler.state.name, len(self._agent_ids), self._max_rounds)
        return True

    # ─── 消息 ─────────────────────────────────────────────────

    async def get_unread_messages(self, agent_id: int) -> List[GtRoomMessage]:
        new_msgs = self._store.get_unread(agent_id)
        if self._scheduler.state != RoomState.INIT:
            await self._scheduler.persist_state()
        return new_msgs

    def has_unread_immediate_messages(self, agent_id: int) -> bool:
        return self._store.has_unread_immediate_messages(agent_id)

    def has_unread_messages(self, agent_id: int) -> bool:
        return self._store.has_unread_messages(agent_id)

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
        # 未装载时 _next_seq=0，会从 0 发号与历史 seq 撞车
        assertUtil.assertTrue(
            self._runtime_state_ready,
            error_message=f"房间运行时状态未装载，拒绝写入: {self.key}",
            error_code="room_state_not_ready",
        )
        state = self._scheduler.state

        # insert_immediately 仅限私聊。注意：**不能**再按 state == IDLE 降级。
        # 旧实现在房间 IDLE 时把 immediate 降级成普通消息，导致「Agent 空闲时发督导指令
        # 不唤醒、要等下一次有人说话」的缺陷 —— 恰恰是最需要立即生效的时刻失效。
        if insert_immediately and self.room_type != RoomType.PRIVATE:
            logger.warning("房间 %s 不支持 immediately 消息 (room_type=%s)，降级为普通消息",
                           self.key, self.room_type)
            insert_immediately = False

        agent = await gtAgentManager.get_agent_by_id(sender_id)
        assert agent, f"agent_id '{sender_id}' not found"

        message = GtRoomMessage(room_id=self.room_id, sender_id=sender_id,
                                content=content, send_time=send_time or datetime.now(),
                                insert_immediately=insert_immediately)
        message.sender_display_name = agent.display_name

        # 唯一写入路径：插入即分配 seq，无论 insert_immediately 与房间状态。
        await self._store.append_and_assign_seq(message, publish=True)

        if state == RoomState.INIT:
            return

        # 调度推进不再区分 immediate / queued：两者走同一条 on_message。
        # - 房间 IDLE 时，on_message 会把状态切到 SCHEDULING 并返回下一位 AI
        #   ⇒ 普通消息和立即注入消息都能唤醒房间。
        # - 房间已在 SCHEDULING 且当前发言人是 AI 时，sender 是 OPERATOR，
        #   on_message 天然返回 None，不会打断当前 Turn。
        if not update_turn_state or not self._agent_ids:
            return
        next_agent_id = self._scheduler.on_message(sender_id)
        if next_agent_id is not None:
            await self._scheduler.persist_state()
            self._scheduler.publish_status(next_agent_id, need_scheduling=True)

    async def escalate_message_to_immediate(self, db_id: int) -> None:
        msg = self._store.escalate_to_immediate(db_id)
        await gtRoomMessageManager.escalate_message_to_immediate(db_id)
        messageBus.publish(MessageBusTopic.ROOM_MSG_CHANGED, gt_room=self.gt_room, gt_message=msg)
        logger.info("消息升级为 immediately: room=%s, db_id=%d", self.key, db_id)

    def publish_read_status(self) -> None:
        """广播最新读游标，供前端把「已读」消息从浮动待读区移入主流。

        显式传当前发言人而不是 None：`publish_status` 的
        `current_turn_agent_id` 参数缺省即 None，直接调用会把前端正在工作的
        指示器（workingAgent）冲掉，看起来像 Agent 突然停了。
        """
        self._scheduler.publish_status(self._build_current_turn_agent_id(), need_scheduling=False)


    # ─── 运行时状态注入与导出 ─────────────────────────────────

    @property
    def runtime_state_ready(self) -> bool:
        """内存运行时态（消息列表 / 读游标 / `_next_seq`）是否已与 DB 对齐过一次。"""
        return self._runtime_state_ready

    def mark_runtime_state_ready(self) -> None:
        self._runtime_state_ready = True

    def inject_runtime_state(
        self,
        messages: List[GtRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
        speaker_index: int | None = None,
    ) -> None:
        self._store.inject(messages=messages, agent_read_index=agent_read_index)
        if speaker_index is not None:
            self._scheduler._current_speaker_index = speaker_index

    def export_agent_read_index(self) -> Dict[int, int]:
        return dict(self._store.get_read_index().items())

    def export_agent_read_index_str(self) -> Dict[str, int]:
        """读游标的对外形态：key 统一为 str，供 API / WS 事件透传给前端。"""
        return {str(k): v for k, v in self._store.get_read_index().items()}

    def mark_all_messages_read(self) -> None:
        self._store.mark_all_read()

    def rebuild_state_from_history(self, persisted_speaker_index: int | None = None) -> None:
        if not self._agent_ids:
            return
        self._scheduler.set_current_speaker_index(persisted_speaker_index)

    # ─── 辅助 ─────────────────────────────────────────────────

    def format_log(self) -> str:
        lines = [f"=== {self.key} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.send_time}] {msg.sender_display_name}: {msg.content}")
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
            "agent_read_index": self.export_agent_read_index_str(),
        }
