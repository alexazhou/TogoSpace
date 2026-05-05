"""房间调度状态机，管理发言位、轮次、跳过窗口和状态转换。"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from constants import RoomState, RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoomManager
from model.dbModel.gtRoom import GtRoom
from service import messageBus
from constants import MessageBusTopic

logger = logging.getLogger("service.roomService")


class RoomScheduler:
    """房间调度状态机。

    ChatRoom 持有 RoomScheduler 实例，将调度逻辑委托给它。
    通过构造函数注入 gt_room（用于发布事件）和 get_read_index（用于持久化）。
    """

    SYSTEM_MEMBER_ID = int(SpecialAgent.SYSTEM.value)
    OPERATOR_MEMBER_ID = int(SpecialAgent.OPERATOR.value)

    def __init__(
        self,
        *,
        agent_ids: List[int],
        room_key: str,
        gt_room: GtRoom,
        get_read_index: Callable[[], Dict[int, int]],
    ):
        self._agent_ids: List[int] = agent_ids
        self._key: str = room_key
        self._gt_room: GtRoom = gt_room
        self._get_read_index = get_read_index

        self._turn_pos: int = 0
        self._turn_count: int = 0
        self._round_skipped_set: set[int] = set()
        self.current_turn_has_content: bool = False
        self._state: RoomState = RoomState.INIT

    # ─── 外部可读属性 ──────────────────────────────────────

    @property
    def state(self) -> RoomState:
        return self._state

    @property
    def turn_pos(self) -> int:
        """当前发言位索引（供持久化等外部使用）。"""
        return self._turn_pos

    def _set_state(self, state: RoomState) -> None:
        """直接设置状态（仅 ChatRoom 在 activate/rebuild 等边界场景使用）。"""
        self._state = state

    def set_turn_pos(self, turn_pos: int | None = None) -> None:
        """从持久化数据恢复发言位，重置跳过窗口。"""
        self._turn_count = 0
        if turn_pos is not None and 0 <= turn_pos < len(self._agent_ids):
            self._turn_pos = turn_pos
        else:
            self._turn_pos = 0
        self._round_skipped_set = set()
        self.current_turn_has_content = False

    # ─── turn 生命周期 ──────────────────────────────────────

    async def finish_turn(self, agent_id: int) -> bool:
        """结束当前发言人：校验 → 记录跳过 → 推进 → 持久化 + 发布。"""
        if self._state == RoomState.INIT:
            logger.warning("房间 %s 仍处于 INIT，拒绝结束轮次", self._key)
            return False
        if agent_id != self._get_current_turn_agent_id():
            logger.warning("房间 %s 拒绝结束轮次申请：agent=%s 并非当前发言人 agent=%s",
                           self._key, gtAgentManager.get_agent_name(agent_id),
                           gtAgentManager.get_agent_name(self._get_current_turn_agent_id()))
            return False

        logger.info(
            "房间 %s 由 agent=%s 结束本轮行动 (has_content=%s, turn_pos=%d/%d, turn_count=%d)",
            self._key, gtAgentManager.get_agent_name(self._get_current_turn_agent_id()),
            self.current_turn_has_content, self._turn_pos, len(self._agent_ids), self._turn_count,
        )

        if not self.current_turn_has_content:
            self._round_skipped_set.add(self._get_current_turn_agent_id())
        self.current_turn_has_content = False

        if not self._agent_ids:
            return True

        self._go_next_turn()
        await self.persist_state()
        if self._is_stop_condition_met():
            self._transition_to_idle_on_stop()
            self.publish_status()
            return True
        next_id = self._advance_to_next_dispatchable()
        if next_id is not None:
            self.publish_status(next_id, need_scheduling=True)
        else:
            if self._state == RoomState.SCHEDULING:
                self._state = RoomState.IDLE
            self.publish_status()
        return True

    def complete_activation(self) -> None:
        """激活收尾：找下一位可调度 Agent → 决定 SCHEDULING/IDLE → 发布。"""
        next_id = self._advance_to_next_dispatchable()
        if next_id is not None:
            self._state = RoomState.SCHEDULING
        self.publish_status(current_turn_agent_id=next_id,
                            need_scheduling=next_id is not None)

    def cancel_current_turn(self) -> None:
        """人工停止 → IDLE。"""
        if self._state != RoomState.SCHEDULING:
            return
        self.current_turn_has_content = False
        self._state = RoomState.IDLE
        logger.info("房间 %s 当前 turn 被人工停止，切回 IDLE 等待新消息唤醒", self._key)
        self.publish_status(current_turn_agent_id=None)

    def wake_up(self, sender_id: int) -> Optional[int]:
        """IDLE/INIT 收到消息 → SCHEDULING，重置轮次，返回下一位可调度 Agent。"""
        was_idle = self._state in (RoomState.IDLE, RoomState.INIT)
        if not was_idle:
            return None

        logger.info("检测到房间 %s 的活动 (agent=%s)，重置轮次计数器并唤醒房间",
                     self._key, gtAgentManager.get_agent_name(sender_id))
        self._turn_count = 0
        self._round_skipped_set = set()
        self.current_turn_has_content = False
        self._state = RoomState.SCHEDULING

        if self._is_stop_condition_met():
            self._transition_to_idle_on_stop()
            return None
        return self._advance_to_next_dispatchable()

    def mark_turn_content(self, sender_id: int) -> None:
        """当前发言人发消息 → 标记 content=True；非当前发言人 → 记插话日志。"""
        current = self._get_current_turn_agent_id()
        if sender_id == current:
            self.current_turn_has_content = True
        else:
            logger.info(
                "房间 %s 收到来自 agent=%s 的插话，保持当前发言位 (当前应轮到 agent=%s)",
                self._key, gtAgentManager.get_agent_name(sender_id),
                gtAgentManager.get_agent_name(current),
            )

    def clear_skip_on_real_message(self, sender_id: int) -> None:
        """收到非系统消息时清空跳过记录，让所有人重新有机会回应。"""
        if sender_id != self.SYSTEM_MEMBER_ID and self._round_skipped_set:
            self._round_skipped_set = set()

    def should_stay_idle_after_wake(self) -> bool:
        """唤醒路径中，如果已进入 IDLE，无需进一步调度。"""
        return self._state == RoomState.IDLE

    def get_current_turn_agent_id(self) -> int:
        return self._get_current_turn_agent_id()

    # ─── 内部方法 ───────────────────────────────────────────

    def _get_current_turn_agent_id(self) -> int:
        assert self._agent_ids, f"房间 {self._key} 没有任何参与者"
        return self._agent_ids[self._turn_pos]

    def _go_next_turn(self) -> None:
        self._turn_pos = (self._turn_pos + 1) % len(self._agent_ids)
        if self._turn_pos == 0:
            self._turn_count += 1

    def _silently_skip(self, agent_id: int) -> None:
        self._round_skipped_set.add(agent_id)
        self.current_turn_has_content = False
        self._go_next_turn()

    def _advance_to_next_dispatchable(self) -> Optional[int]:
        """从当前发言位向前推进，跳过不可调度的成员（如 GROUP 中的 OPERATOR）。
        遇到 SpecialAgent 等待输入时返回 None。"""
        if not self._agent_ids:
            return None

        while True:
            agent_id = self._get_current_turn_agent_id()

            if self._should_auto_skip():
                self._silently_skip(agent_id)
                if self._is_stop_condition_met():
                    self._transition_to_idle_on_stop()
                    return None
                continue

            if self._is_special_agent(agent_id):
                logger.info(
                    "当前发言位为特殊成员，等待外部输入: room=%s, agent=%s",
                    self._key, gtAgentManager.get_agent_name(agent_id),
                )
                return None

            return agent_id

    def _should_auto_skip(self) -> bool:
        agent_id = self._get_current_turn_agent_id()
        return (
            agent_id == self.OPERATOR_MEMBER_ID
            and self._gt_room.type == RoomType.GROUP
            and len(self._agent_ids) > 2
        )

    def _is_special_agent(self, agent_id: int) -> bool:
        return agent_id in (self.SYSTEM_MEMBER_ID, self.OPERATOR_MEMBER_ID)

    def _is_stop_condition_met(self) -> bool:
        if self._gt_room.max_turns > 0 and self._turn_count >= self._gt_room.max_turns:
            return True
        ai_agent_ids = {aid for aid in self._agent_ids if aid != self.OPERATOR_MEMBER_ID}
        return bool(ai_agent_ids) and ai_agent_ids.issubset(self._round_skipped_set)

    def _transition_to_idle_on_stop(self) -> None:
        if self._state == RoomState.IDLE:
            return
        self._state = RoomState.IDLE
        if self._gt_room.max_turns > 0 and self._turn_count >= self._gt_room.max_turns:
            logger.info("房间 %s 已达到最大轮次 %d，进入 IDLE 状态", self._key, self._gt_room.max_turns)
        else:
            logger.info("房间 %s 所有 AI 成员均已跳过发言，停止调度", self._key)
        self.publish_status(current_turn_agent_id=None)

    # ─── 外部动作 ───────────────────────────────────────────

    def publish_status(self, current_turn_agent_id: int | None = None, *,
                       need_scheduling: bool = False) -> None:
        """广播房间状态，不推送 INIT 状态。"""
        if self._state == RoomState.INIT:
            return
        messageBus.publish(
            MessageBusTopic.ROOM_STATUS_CHANGED,
            gt_room=self._gt_room,
            state=self._state,
            current_turn_agent_id=current_turn_agent_id,
            need_scheduling=need_scheduling,
        )

    async def persist_state(self) -> None:
        """持久化 turn_pos 与各 Agent 已读进度。"""
        if self._state == RoomState.INIT:
            return
        id_keyed = {str(k): v for k, v in self._get_read_index().items()}
        await gtRoomManager.update_room_state(self._gt_room.id, id_keyed, self._turn_pos)
