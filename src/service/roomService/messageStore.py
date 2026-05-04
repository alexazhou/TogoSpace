from __future__ import annotations

from typing import Dict, List

from model.coreModel.gtCoreChatModel import GtCoreRoomMessage


class RoomMessageStore:
    """管理房间内存消息列表与各 Agent 已读进度。

    消息分两条轨道：
    - _messages：已进入主流（seq 已赋值），供 get_unread() 使用。
    - _pending_messages：用户发送的 immediately 消息，seq=None，等待
      agentTurnRunner 在安全边界调用 flush_pending_immediate() 后进入主流。
    """

    def __init__(self, agent_ids: List[int]):
        self._messages: List[GtCoreRoomMessage] = []
        self._agent_read_index: Dict[int, int] = {}
        self._agent_ids: List[int] = agent_ids
        self._next_seq: int = 0
        self._pending_messages: List[GtCoreRoomMessage] = []

    @property
    def messages(self) -> List[GtCoreRoomMessage]:
        return self._messages

    @property
    def pending_messages(self) -> List[GtCoreRoomMessage]:
        return self._pending_messages

    def append_and_assign_seq(self, msg: GtCoreRoomMessage) -> None:
        """追加到主消息列表，并自动分配 seq。"""
        msg.seq = self._next_seq
        self._next_seq += 1
        self._messages.append(msg)

    def append_pending(self, msg: GtCoreRoomMessage) -> None:
        """将 immediately 消息放入等待注入队列（seq 尚未分配）。"""
        self._pending_messages.append(msg)

    def get_unread(self, agent_id: int) -> List[GtCoreRoomMessage]:
        """返回 agent_id 尚未读取的新消息，并推进其读取位置。"""
        read_idx = self._agent_read_index.get(agent_id, 0)
        new_msgs = self._messages[read_idx:]
        self._agent_read_index[agent_id] = len(self._messages)
        return new_msgs

    def mark_all_read(self) -> None:
        tail = len(self._messages)
        self._agent_read_index = {aid: tail for aid in self._agent_ids}

    def inject(
        self,
        messages: List[GtCoreRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
    ) -> None:
        if messages is not None:
            main: List[GtCoreRoomMessage] = []
            pending: List[GtCoreRoomMessage] = []
            max_seq = -1
            for msg in messages:
                if msg.seq is None:
                    pending.append(msg)
                else:
                    main.append(msg)
                    if msg.seq > max_seq:
                        max_seq = msg.seq
            self._messages = main
            self._pending_messages = pending
            self._next_seq = max_seq + 1 if max_seq >= 0 else 0
        if agent_read_index is not None:
            converted: Dict[int, int] = {}
            for k, v in agent_read_index.items():
                try:
                    converted[int(k)] = v
                except (ValueError, TypeError):
                    pass  # 忽略无效的 key
            self._agent_read_index = converted

    def has_pending_immediate_messages(self, agent_id: int) -> bool:
        """检查 immediately 注入队列中是否有待处理消息。"""
        return bool(self._pending_messages)

    def flush_pending_immediate(self) -> List[GtCoreRoomMessage]:
        """将注入队列中的所有消息移入主消息列表并分配 seq，返回已移入的消息列表。"""
        if not self._pending_messages:
            return []
        flushed = list(self._pending_messages)
        self._pending_messages.clear()
        for msg in flushed:
            msg.seq = self._next_seq
            self._next_seq += 1
            self._messages.append(msg)
        return flushed

    def get_read_index(self) -> Dict[int, int]:
        """返回当前读取进度字典（供持久化使用）。"""
        return self._agent_read_index

