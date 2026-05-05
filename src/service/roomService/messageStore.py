from __future__ import annotations

import logging
from typing import Dict, List

from dal.db import gtRoomMessageManager

logger = logging.getLogger("service.roomService")
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from model.dbModel.gtRoom import GtRoom
from service import messageBus
from constants import MessageBusTopic


class RoomMessageStore:
    """管理房间内存消息列表、各 Agent 已读进度、DB 持久化与 WS 事件广播。"""

    def __init__(self, agent_ids: List[int], *, gt_room: GtRoom):
        self._messages: List[GtCoreRoomMessage] = []
        self._agent_seq_read: Dict[int, int] = {}  # agent_id -> 下一个待读的 seq（不含）
        self._agent_ids: List[int] = agent_ids
        self._next_seq: int = 0
        self._gt_room: GtRoom = gt_room

    @property
    def messages(self) -> List[GtCoreRoomMessage]:
        """返回已分配 seq 的主流消息列表。"""
        return [m for m in self._messages if m.seq is not None]

    @property
    def pending_messages(self) -> List[GtCoreRoomMessage]:
        """返回尚未注入的 pending 消息列表（seq=None）。"""
        return [m for m in self._messages if m.seq is None]

    def append_and_assign_seq(self, msg: GtCoreRoomMessage) -> None:
        """追加到主消息列表，并自动分配 seq。

        维持不变量：seq 已赋值的消息排在所有 seq=None 消息之前。
        """
        msg.seq = self._next_seq
        self._next_seq += 1
        insert_pos = next((i for i, m in enumerate(self._messages) if m.seq is None), len(self._messages))
        self._messages.insert(insert_pos, msg)

    def append_pending(self, msg: GtCoreRoomMessage) -> None:
        """将消息追加到 pending 队列末尾（seq 尚未分配）。

        适用于两类 pending 消息：
        - insert_immediately=True：待注入消息，等待在安全边界 flush_pending_immediate() 注入。
        - insert_immediately=False：queued 消息，等待轮次结束后 flush_queued() 注入。
        """
        assert msg.seq is None, f"append_pending 要求 seq 为 None，实际为 {msg.seq}"
        self._messages.append(msg)

    def get_unread(self, agent_id: int) -> List[GtCoreRoomMessage]:
        """返回 agent_id 尚未读取的主流消息，并推进其读取进度。"""
        next_seq = self._agent_seq_read.get(agent_id, 0)
        new_msgs = [m for m in self._messages if m.seq is not None and m.seq >= next_seq]
        if new_msgs:
            self._agent_seq_read[agent_id] = new_msgs[-1].seq + 1
        return new_msgs

    def mark_all_read(self) -> None:
        self._agent_seq_read = {aid: self._next_seq for aid in self._agent_ids}

    def _sort(self) -> None:
        """维持不变量：seq 已赋值消息在前（按 seq 升序），seq=None 消息在后（按 db_id 升序）。

        排序 key 是三元组 (is_pending, seq_or_zero, db_id_or_zero)：
        - is_pending：False(0) < True(1)，确保有 seq 的消息整体排在 seq=None 消息之前
        - seq_or_zero：有 seq 时按 seq 升序；seq=None 时填 0 占位（不参与此组排序）
        - db_id_or_zero：seq=None 的消息按 db_id 升序；有 seq 时填 0 占位（不参与此组排序）
        """
        self._messages.sort(key=lambda m: (
            m.seq is None,
            m.seq if m.seq is not None else 0,
            m.db_id if m.seq is None and m.db_id is not None else 0,
        ))

    def inject(
        self,
        messages: List[GtCoreRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
    ) -> None:
        if messages is not None:
            self._messages = list(messages)
            self._sort()
            seq_msgs = [m for m in self._messages if m.seq is not None]
            self._next_seq = seq_msgs[-1].seq + 1 if seq_msgs else 0  # type: ignore[operator]
        if agent_read_index is not None:
            converted: Dict[int, int] = {}
            for k, v in agent_read_index.items():
                try:
                    converted[int(k)] = v
                except (ValueError, TypeError):
                    pass  # 忽略无效的 key
            self._agent_seq_read = converted

    def has_pending_immediate_messages(self, agent_id: int) -> bool:
        """检查是否有 insert_immediately=True 且 seq=None 的待注入消息。"""
        return any(m.seq is None and m.insert_immediately for m in self._messages)

    async def flush_pending_immediate(self) -> List[GtCoreRoomMessage]:
        """将 insert_immediately=True 的 pending 消息分配 seq 并更新 DB，返回已处理列表。"""
        return await self._flush(immediate_only=True)

    async def flush_queued(self) -> List[GtCoreRoomMessage]:
        """将 insert_immediately=False 的 pending 消息分配 seq 并更新 DB，返回已处理列表。"""
        return await self._flush(immediate_only=False)

    async def _flush(self, *, immediate_only: bool) -> List[GtCoreRoomMessage]:
        kind = "immediately" if immediate_only else "queued"
        pending = [m for m in self._messages if m.seq is None and m.insert_immediately == immediate_only]
        if not pending:
            return []
        for msg in pending:
            msg.seq = self._next_seq
            self._next_seq += 1
            if msg.db_id is not None:
                await gtRoomMessageManager.update_room_message_seq(msg.db_id, msg.seq)  # type: ignore[arg-type]
            messageBus.publish(MessageBusTopic.ROOM_MSG_CHANGED, gt_room=self._gt_room, gt_message=msg)
        logger.info("%s 消息注入完成: room=%s, count=%d, seqs=%s",
                     kind, self._gt_room.name, len(pending), [m.seq for m in pending])
        return pending

    def escalate_to_immediate(self, db_id: int) -> GtCoreRoomMessage:
        """将消息升级为 pending immediately。

        支持两类消息：
        1. 主流未读消息（seq!=None）：移出主流，seq 清空，标记为 immediately。
        2. pending queued 消息（seq=None 且 insert_immediately=False）：原地改为 immediately。

        若消息不存在，抛出 ValueError。
        若主流消息已被 agent 读取，抛出 RuntimeError。
        """
        msg = next((m for m in self._messages if m.db_id == db_id), None)
        if msg is None:
            raise ValueError(f"message db_id={db_id} not found")

        if msg.seq is not None:
            for agent_id in self._agent_ids:
                if self._agent_seq_read.get(agent_id, 0) > msg.seq:  # type: ignore[operator]
                    raise RuntimeError(f"message db_id={db_id} already read by agent_id={agent_id}")
            msg.seq = None

        msg.insert_immediately = True
        self._sort()
        return msg

    def get_read_index(self) -> Dict[int, int]:
        """返回当前读取进度字典（供持久化使用）。"""
        return self._agent_seq_read
