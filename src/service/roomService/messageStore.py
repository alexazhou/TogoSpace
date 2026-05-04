from __future__ import annotations

from typing import Dict, List

from model.coreModel.gtCoreChatModel import GtCoreRoomMessage


class RoomMessageStore:
    """管理房间内存消息列表与各 Agent 已读进度。

    所有消息统一存储在 _messages 中：
    - seq 已赋值的消息排在前面（按 seq 升序），为主流消息，供 get_unread() 使用。
    - seq=None 的消息（insert_immediately=True）排在末尾，为 pending 状态，
      等待 agentTurnRunner 在安全边界调用 flush_pending_immediate() 后进入主流。
    """

    def __init__(self, agent_ids: List[int]):
        self._messages: List[GtCoreRoomMessage] = []
        self._agent_seq_read: Dict[int, int] = {}  # agent_id -> 下一个待读的 seq（不含）
        self._agent_ids: List[int] = agent_ids
        self._next_seq: int = 0

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
        """将 immediately 消息追加到列表末尾（seq 尚未分配）。"""
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
        """检查是否有 seq=None 的待处理消息。"""
        return any(m.seq is None for m in self._messages)

    def flush_pending_immediate(self) -> List[GtCoreRoomMessage]:
        """将 seq=None 的消息分配 seq，使其进入主流，返回已处理的消息列表。"""
        pending = [m for m in self._messages if m.seq is None]
        if not pending:
            return []
        for msg in pending:
            msg.seq = self._next_seq
            self._next_seq += 1
        return pending

    def escalate_to_immediate(self, db_id: int) -> GtCoreRoomMessage:
        """将主流中尚未被任何 agent 读取的消息升级为 pending（seq=None）。

        升级后消息移至列表末尾，seq 清空，insert_immediately 标记为 True。
        若消息不存在于主流中，抛出 ValueError。
        若消息已被 agent 读取，抛出 RuntimeError。
        """
        msg = next((m for m in self._messages if m.db_id == db_id and m.seq is not None), None)
        if msg is None:
            raise ValueError(f"message db_id={db_id} not found in main stream")
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

