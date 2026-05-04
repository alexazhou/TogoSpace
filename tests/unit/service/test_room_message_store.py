"""RoomMessageStore 单元测试：测试纯内存操作（不依赖数据库）。"""
from __future__ import annotations

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
        """flush_pending_immediate 将消息移入主列表并分配 seq。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_and_assign_seq(_msg(content="before"))  # seq=0
        msg = _msg(insert_immediately=True, content="immediate")
        store.append_pending(msg)

        flushed = store.flush_pending_immediate()
        assert len(flushed) == 1
        assert flushed[0].seq == 1  # 紧接在 seq=0 之后
        assert len(store.messages) == 2
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_flush_messages_appear_in_get_unread(self):
        """flush 后 immediately 消息可通过 get_unread 读取。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append_pending(_msg(insert_immediately=True))
        store.flush_pending_immediate()
        unread = store.get_unread(agent_id=1)
        assert len(unread) == 1
        assert unread[0].insert_immediately is True

    def test_global_queue_cleared_for_all_agents(self):
        """pending 队列是房间级别的，flush 后所有 agent 都看不到 pending。"""
        store = RoomMessageStore(agent_ids=[1, 2])
        store.append_pending(_msg(insert_immediately=True))
        store.flush_pending_immediate()
        assert store.has_pending_immediate_messages(agent_id=1) is False
        assert store.has_pending_immediate_messages(agent_id=2) is False

