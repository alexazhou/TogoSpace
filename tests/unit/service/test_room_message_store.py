"""RoomMessageStore 单元测试：测试纯内存操作（不依赖数据库）。"""
from __future__ import annotations

import pytest
from datetime import datetime

from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from service.roomService.messageStore import RoomMessageStore


def _msg(sender_id: int = 1, content: str = "msg", *, insert_immediately: bool = False) -> GtCoreRoomMessage:
    return GtCoreRoomMessage(
        sender_id=sender_id,
        sender_display_name="Sender",
        content=content,
        send_time=datetime(2024, 1, 1),
        insert_immediately=insert_immediately,
    )


class TestHasPendingImmediateMessages:
    """has_pending_immediate_messages：immediately 消息在 pending 队列中的检测行为。"""

    def test_returns_false_when_no_messages(self):
        store = RoomMessageStore(agent_ids=[1])
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_returns_false_for_regular_unread_messages(self):
        store = RoomMessageStore(agent_ids=[1])
        store.append_and_assign_seq(_msg(insert_immediately=False))
        store.append_and_assign_seq(_msg(insert_immediately=False))
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_returns_true_for_pending_immediate_message(self):
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=True))
        assert store.has_pending_immediate_messages(agent_id=1) is True

    def test_returns_false_after_flush(self):
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=True))
        store.flush_pending_immediate()
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_returns_false_for_pending_queued_message(self):
        """queued 消息（insert_immediately=False）不影响 has_pending_immediate_messages。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=False))
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_does_not_advance_read_index(self):
        """has_pending_immediate_messages 只检查，不推进游标。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=True))
        store.has_pending_immediate_messages(agent_id=1)
        store.has_pending_immediate_messages(agent_id=1)
        # 还未 flush，get_unread 不含 pending 消息
        unread = store.get_unread(agent_id=1)
        assert len(unread) == 0

    def test_flush_moves_to_main_list_and_assigns_seq(self):
        """flush_pending_immediate 只将 insert_immediately=True 的消息移入主列表并分配 seq。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_and_assign_seq(_msg(content="before"))  # seq=0
        msg = _msg(insert_immediately=True, content="immediate")
        store.append_pending(msg)
        queued = _msg(insert_immediately=False, content="queued")
        store.append_pending(queued)

        flushed = store.flush_pending_immediate()
        assert len(flushed) == 1
        assert flushed[0].seq == 1  # 紧接在 seq=0 之后
        assert len(store.messages) == 2  # before + immediate
        assert store.has_pending_immediate_messages(agent_id=1) is False
        assert len(store.pending_messages) == 1  # queued 仍在 pending

    def test_flush_messages_appear_in_get_unread(self):
        """flush 后 immediately 消息可通过 get_unread 读取。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=True))
        store.flush_pending_immediate()
        unread = store.get_unread(agent_id=1)
        assert len(unread) == 1
        assert unread[0].insert_immediately is True

    def test_global_queue_cleared_for_all_agents(self):
        """pending 队列是房间级别的，flush 后所有 agent 都看不到 pending immediate 消息。"""
        store = RoomMessageStore(agent_ids=[1, 2])
        store.append_pending(_msg(insert_immediately=True))
        store.flush_pending_immediate()
        assert store.has_pending_immediate_messages(agent_id=1) is False
        assert store.has_pending_immediate_messages(agent_id=2) is False


class TestFlushQueued:
    """flush_queued()：只处理 insert_immediately=False 的 pending 消息。"""

    def test_flush_queued_assigns_seq_to_queued_only(self):
        """flush_queued 只对 insert_immediately=False 的 pending 消息分配 seq。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_and_assign_seq(_msg(content="before"))  # seq=0
        queued = _msg(insert_immediately=False, content="queued")
        immediate = _msg(insert_immediately=True, content="immediate")
        store.append_pending(queued)
        store.append_pending(immediate)

        flushed = store.flush_queued()
        assert len(flushed) == 1
        assert flushed[0].content == "queued"
        assert flushed[0].seq == 1
        # immediate 消息仍在 pending
        assert store.has_pending_immediate_messages(agent_id=1) is True
        assert len(store.pending_messages) == 1

    def test_flush_queued_returns_empty_when_none(self):
        """无 queued 消息时返回空列表。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=True))
        result = store.flush_queued()
        assert result == []

    def test_flush_queued_seq_continues_from_main_list(self):
        """queued 消息的 seq 紧接在已有主流消息之后。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_and_assign_seq(_msg(content="a"))  # seq=0
        store.append_and_assign_seq(_msg(content="b"))  # seq=1
        queued = _msg(insert_immediately=False, content="queued")
        store.append_pending(queued)

        flushed = store.flush_queued()
        assert flushed[0].seq == 2

    def test_flush_queued_multiple_messages(self):
        """多条 queued 消息按追加顺序分配 seq。"""
        store = RoomMessageStore(agent_ids=[1])
        d1 = _msg(insert_immediately=False, content="d1")
        d2 = _msg(insert_immediately=False, content="d2")
        store.append_pending(d1)
        store.append_pending(d2)

        flushed = store.flush_queued()
        assert len(flushed) == 2
        assert flushed[0].seq == 0
        assert flushed[1].seq == 1

    def test_flush_queued_messages_appear_in_get_unread(self):
        """flush 后 queued 消息可通过 get_unread 读取。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=False))
        store.flush_queued()
        unread = store.get_unread(agent_id=1)
        assert len(unread) == 1
        assert unread[0].insert_immediately is False


class TestEscalateToImmediate:
    """escalate_to_immediate：将主列表中未读消息升级为 pending immediately 消息。"""

    def _msg_with_db_id(self, db_id: int, content: str = "msg") -> GtCoreRoomMessage:
        m = _msg(content=content)
        m.db_id = db_id
        return m

    def test_escalate_unread_message_succeeds(self):
        """未被任何 agent 读取的消息可以升级。"""
        store = RoomMessageStore(agent_ids=[1])
        m = self._msg_with_db_id(db_id=10)
        store.append_and_assign_seq(m)

        result = store.escalate_to_immediate(db_id=10)

        assert result.seq is None
        assert result.insert_immediately is True
        assert len(store.messages) == 0
        assert len(store.pending_messages) == 1

    def test_escalate_raises_if_db_id_not_in_main_list(self):
        """db_id 不在主列表中（不存在或已在 pending）时抛出 ValueError。"""
        store = RoomMessageStore(agent_ids=[1])
        with pytest.raises(ValueError):
            store.escalate_to_immediate(db_id=999)

    def test_escalate_raises_if_agent_already_read(self):
        """agent 已读取过该消息后，升级应抛出 RuntimeError。"""
        store = RoomMessageStore(agent_ids=[1])
        m = self._msg_with_db_id(db_id=10)
        store.append_and_assign_seq(m)
        store.get_unread(agent_id=1)  # agent reads it

        with pytest.raises(RuntimeError):
            store.escalate_to_immediate(db_id=10)

        assert len(store.messages) == 1  # unchanged

    def test_escalate_unread_message_among_multiple_agents(self):
        """多 agent 场景：所有 agent 都未读时可升级。"""
        store = RoomMessageStore(agent_ids=[1, 2])
        m = self._msg_with_db_id(db_id=10)
        store.append_and_assign_seq(m)

        result = store.escalate_to_immediate(db_id=10)
        assert result.seq is None

    def test_escalate_raises_if_any_agent_already_read(self):
        """只要有一个 agent 已读，升级就应抛出 RuntimeError。"""
        store = RoomMessageStore(agent_ids=[1, 2])
        m = self._msg_with_db_id(db_id=10)
        store.append_and_assign_seq(m)
        store.get_unread(agent_id=1)  # agent 1 reads it, agent 2 has not

        with pytest.raises(RuntimeError):
            store.escalate_to_immediate(db_id=10)

    def test_escalate_preserves_other_messages_and_read_index(self):
        """升级一条消息后，其他消息顺序与 agent 读取进度不受影响。"""
        store = RoomMessageStore(agent_ids=[1])
        m0 = self._msg_with_db_id(db_id=5, content="before")
        m1 = self._msg_with_db_id(db_id=10, content="target")
        m2 = self._msg_with_db_id(db_id=15, content="after")
        store.append_and_assign_seq(m0)
        store.append_and_assign_seq(m1)
        store.append_and_assign_seq(m2)

        store.escalate_to_immediate(db_id=10)

        assert len(store.messages) == 2
        assert store.messages[0].db_id == 5
        assert store.messages[1].db_id == 15
        unread = store.get_unread(agent_id=1)
        assert [m.db_id for m in unread] == [5, 15]


class TestSort:
    """_sort()：pending 消息按 db_id 升序排，主流消息按 seq 升序在前。"""

    def _msg_with_db_id(self, db_id: int) -> GtCoreRoomMessage:
        m = _msg()
        m.db_id = db_id
        return m

    def test_pending_messages_sorted_by_db_id(self):
        """多条 pending 消息应按 db_id 升序排列。"""
        store = RoomMessageStore(agent_ids=[])
        m_high = self._msg_with_db_id(db_id=20)
        m_low = self._msg_with_db_id(db_id=5)
        m_mid = self._msg_with_db_id(db_id=10)
        store.append_pending(m_high)
        store.append_pending(m_low)
        store.append_pending(m_mid)

        # 触发一次排序（escalate 或 inject 都会触发；这里直接调用私有方法验证）
        store._sort()  # noqa: SLF001

        assert [m.db_id for m in store.pending_messages] == [5, 10, 20]

    def test_seq_messages_come_before_pending(self):
        """seq 已赋值的消息整体排在 pending 消息前面。"""
        store = RoomMessageStore(agent_ids=[])
        pending = self._msg_with_db_id(db_id=1)
        pending.seq = None
        seq_msg = self._msg_with_db_id(db_id=99)
        seq_msg.seq = 0
        store._messages = [pending, seq_msg]  # noqa: SLF001

        store._sort()  # noqa: SLF001

        assert store._messages[0].seq == 0  # noqa: SLF001
        assert store._messages[1].seq is None  # noqa: SLF001

    def test_escalate_multiple_pending_sorted_by_db_id(self):
        """连续 escalate 后 pending 列表按 db_id 排序。"""
        store = RoomMessageStore(agent_ids=[])
        m1 = self._msg_with_db_id(db_id=30)
        m2 = self._msg_with_db_id(db_id=10)
        m3 = self._msg_with_db_id(db_id=20)
        for m in (m1, m2, m3):
            store.append_and_assign_seq(m)

        store.escalate_to_immediate(db_id=30)
        store.escalate_to_immediate(db_id=10)
        store.escalate_to_immediate(db_id=20)

        assert [m.db_id for m in store.pending_messages] == [10, 20, 30]

