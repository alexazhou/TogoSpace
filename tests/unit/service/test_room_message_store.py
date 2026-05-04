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
    """has_pending_immediate_messages 在不同消息状态下的行为。"""

    def test_returns_false_when_no_messages(self):
        store = RoomMessageStore(agent_ids=[1])
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_returns_false_for_regular_unread_messages(self):
        store = RoomMessageStore(agent_ids=[1])
        store.append(_msg(insert_immediately=False))
        store.append(_msg(insert_immediately=False))
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_returns_true_for_unread_immediate_message(self):
        store = RoomMessageStore(agent_ids=[1])
        store.append(_msg(insert_immediately=True))
        assert store.has_pending_immediate_messages(agent_id=1) is True

    def test_returns_false_after_messages_are_read(self):
        store = RoomMessageStore(agent_ids=[1])
        store.append(_msg(insert_immediately=True))
        store.get_unread(agent_id=1)  # 推进已读游标
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_does_not_advance_read_index(self):
        """has_pending_immediate_messages 只检查，不推进游标。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append(_msg(insert_immediately=True))
        store.has_pending_immediate_messages(agent_id=1)
        store.has_pending_immediate_messages(agent_id=1)
        # 游标未推进，get_unread 仍能拿到该消息
        unread = store.get_unread(agent_id=1)
        assert len(unread) == 1

    def test_only_checks_unread_slice(self):
        """已读消息的 insert_immediately 标志不影响检查结果。"""
        store = RoomMessageStore(agent_ids=[1])
        store.append(_msg(insert_immediately=True))
        store.get_unread(agent_id=1)  # 推进游标，上面那条变已读
        store.append(_msg(insert_immediately=False))
        assert store.has_pending_immediate_messages(agent_id=1) is False

    def test_independent_per_agent(self):
        """不同 agent 的已读游标独立。"""
        store = RoomMessageStore(agent_ids=[1, 2])
        store.append(_msg(insert_immediately=True))
        store.get_unread(agent_id=1)  # agent 1 读过了
        assert store.has_pending_immediate_messages(agent_id=1) is False
        assert store.has_pending_immediate_messages(agent_id=2) is True
