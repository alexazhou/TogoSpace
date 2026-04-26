from __future__ import annotations

from typing import Dict, List

from model.coreModel.gtCoreChatModel import GtCoreRoomMessage


class RoomMessageStore:
    """管理房间内存消息列表与各 Agent 已读进度。"""

    def __init__(self, agent_ids: List[int]):
        self._messages: List[GtCoreRoomMessage] = []
        self._agent_read_index: Dict[int, int] = {}
        self._agent_ids: List[int] = agent_ids

    @property
    def messages(self) -> List[GtCoreRoomMessage]:
        return self._messages

    def append(self, msg: GtCoreRoomMessage) -> None:
        self._messages.append(msg)

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
            self._messages = list(messages)
        if agent_read_index is not None:
            converted: Dict[int, int] = {}
            for k, v in agent_read_index.items():
                try:
                    converted[int(k)] = v
                except (ValueError, TypeError):
                    pass  # 忽略无效的 key
            self._agent_read_index = converted

    def get_read_index(self) -> Dict[int, int]:
        """返回当前读取进度字典（供持久化使用）。"""
        return self._agent_read_index
