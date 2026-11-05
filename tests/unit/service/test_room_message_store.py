"""RoomMessageStore 单元测试：测试纯内存操作（不依赖数据库）。

本文件覆盖三个正交职责（见 messageStore.py 类 docstring）：

1. 顺序（seq）：写入即分配，无 NULL 中间态；
2. 未读（_agent_seq_read）：游标语义，且「是否有未读」必须排除自身消息；
3. 立即注入（insert_immediately）：只是标志位，不参与排序与可见性。

旧状态机（append_pending / flush_pending_immediate / flush_queued / _sort）已退役，
不在本文件测试范围。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from model.dbModel.gtRoomMessage import GtRoomMessage
from service.roomService.messageStore import RoomMessageStore

OPERATOR_ID = -1  # constants.SpecialAgent.OPERATOR
SYSTEM_ID = -2  # constants.SpecialAgent.SYSTEM
AGENT_ID = 1


@pytest.fixture(autouse=True)
def mock_db_manager():
    with patch("service.roomService.messageStore.gtRoomMessageManager") as mock:
        mock.append_room_message = AsyncMock(return_value=MagicMock(id=999))
        yield mock


@pytest.fixture(autouse=True)
def mock_message_bus():
    with patch("service.roomService.messageStore.messageBus") as mock:
        mock.publish = MagicMock()
        yield mock


@pytest.fixture
def mock_room():
    return MagicMock(id=1, name="test_room", agent_ids=[AGENT_ID])


def _msg(sender_id: int = AGENT_ID, content: str = "msg", *,
         insert_immediately: bool = False, db_id: int | None = None) -> GtRoomMessage:
    m = GtRoomMessage(room_id=1, sender_id=sender_id, content=content,
                      send_time=datetime(2024, 1, 1), insert_immediately=insert_immediately)
    m.sender_display_name = "Sender"
    if db_id is not None:
        m.id = db_id
    return m


# --------------------------------------------------------------------------- #
# 1. seq 分配：唯一写入路径 append_and_assign_seq
# --------------------------------------------------------------------------- #
class TestAppendAndAssignSeq:
    """append_and_assign_seq：追加即分配 seq，并透传 insert_immediately 落库。"""

    async def test_assigns_seq_from_zero_in_write_order(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        a, b, c = _msg(content="a"), _msg(content="b"), _msg(content="c")
        await store.append_and_assign_seq(a)
        await store.append_and_assign_seq(b)
        await store.append_and_assign_seq(c)
        assert [m.seq for m in store.messages] == [0, 1, 2]

    async def test_seq_is_assigned_immediately_no_null_state(self, mock_room):
        """不存在「seq 待分配」的中间态：写入返回后 seq 已就绪。"""
        store = RoomMessageStore(gt_room=mock_room)
        m = _msg()
        await store.append_and_assign_seq(m)
        assert m.seq == 0
        assert all(x.seq is not None for x in store.messages)

    async def test_persists_message_and_backfills_db_id(self, mock_room, mock_db_manager):
        store = RoomMessageStore(gt_room=mock_room)
        m = _msg(content="hello")
        await store.append_and_assign_seq(m)

        mock_db_manager.append_room_message.assert_awaited_once()
        kwargs = mock_db_manager.append_room_message.await_args.kwargs
        assert kwargs["room_id"] == 1
        assert kwargs["content"] == "hello"
        assert kwargs["seq"] == 0
        assert m.id == 999

    async def test_passes_insert_immediately_flag_to_db(self, mock_room, mock_db_manager):
        """旧实现硬编码 False，导致重启后立即注入标志丢失；此处必须透传。"""
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(insert_immediately=True))
        kwargs = mock_db_manager.append_room_message.await_args.kwargs
        assert kwargs["insert_immediately"] is True

    async def test_skips_db_write_when_message_already_has_id(self, mock_room, mock_db_manager):
        """已落库的消息（如从 DB 取出后重新追加）不应重复插入。"""
        store = RoomMessageStore(gt_room=mock_room)
        m = _msg(db_id=42)
        await store.append_and_assign_seq(m)
        mock_db_manager.append_room_message.assert_not_awaited()
        assert m.id == 42
        assert m.seq == 0

    async def test_publish_broadcasts_when_requested(self, mock_room, mock_message_bus):
        store = RoomMessageStore(gt_room=mock_room)
        m = _msg()
        await store.append_and_assign_seq(m, publish=True)
        mock_message_bus.publish.assert_called_once()

    async def test_no_broadcast_by_default(self, mock_room, mock_message_bus):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg())
        mock_message_bus.publish.assert_not_called()

    async def test_messages_property_returns_all_regardless_of_flag(self, mock_room):
        """messages 不再按 seq 过滤：普通消息与 immediate 消息一视同仁。"""
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(content="plain"))
        await store.append_and_assign_seq(_msg(content="immediate", insert_immediately=True))
        assert [m.content for m in store.messages] == ["plain", "immediate"]

    async def test_messages_returns_copy(self, mock_room):
        """返回值是副本，调用方改动不影响内部列表。"""
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg())
        snapshot = store.messages
        snapshot.clear()
        assert len(store.messages) == 1


# --------------------------------------------------------------------------- #
# 2. 读游标：get_unread 推进游标，且包含自身消息
# --------------------------------------------------------------------------- #
class TestGetUnread:
    """get_unread：取 seq >= 游标 的全部消息，并推进游标。"""

    def test_returns_empty_when_no_messages(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        assert store.get_unread(agent_id=AGENT_ID) == []

    async def test_returns_all_from_cursor_start(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(content="a"))
        await store.append_and_assign_seq(_msg(content="b"))
        assert [m.seq for m in store.get_unread(agent_id=AGENT_ID)] == [0, 1]

    async def test_advances_cursor_so_second_call_is_empty(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg())
        store.get_unread(agent_id=AGENT_ID)
        assert store.get_unread(agent_id=AGENT_ID) == []

    async def test_returns_only_new_messages_after_cursor(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(content="old"))
        store.get_unread(agent_id=AGENT_ID)
        await store.append_and_assign_seq(_msg(content="new"))
        unread = store.get_unread(agent_id=AGENT_ID)
        assert [m.content for m in unread] == ["new"]

    async def test_includes_own_messages(self, mock_room):
        """游标必须能越过自己的发言，否则会被自己的消息卡住。"""
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=AGENT_ID, content="mine"))
        assert [m.content for m in store.get_unread(agent_id=AGENT_ID)] == ["mine"]

    async def test_cursor_is_per_agent(self, mock_room):
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=3))
        store.get_unread(agent_id=1)
        assert len(store.get_unread(agent_id=2)) == 1

    async def test_read_index_reflects_next_seq(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg())
        await store.append_and_assign_seq(_msg())
        store.get_unread(agent_id=AGENT_ID)
        assert store.get_read_index()[AGENT_ID] == 2


# --------------------------------------------------------------------------- #
# 3. 未读判定：has_unread_messages / has_unread_immediate_messages 必须排除自身
# --------------------------------------------------------------------------- #
class TestHasUnreadMessages:
    """has_unread_messages：只判断，不推进游标；必须排除自己发的消息。"""

    def test_false_when_no_messages(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        assert store.has_unread_messages(AGENT_ID) is False

    async def test_true_for_unread_other_message(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2))
        assert store.has_unread_messages(AGENT_ID) is True

    async def test_false_after_read(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2))
        store.get_unread(agent_id=AGENT_ID)
        assert store.has_unread_messages(AGENT_ID) is False

    async def test_false_for_own_message_only(self, mock_room):
        """AI 自己刚发言时不能误判为「还有未读」，否则 Turn 结束会空转。"""
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=AGENT_ID))
        assert store.has_unread_messages(AGENT_ID) is False

    async def test_true_when_own_message_followed_by_other(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=AGENT_ID))
        await store.append_and_assign_seq(_msg(sender_id=2))
        assert store.has_unread_messages(AGENT_ID) is True

    async def test_does_not_advance_cursor(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2))
        store.has_unread_messages(AGENT_ID)
        store.has_unread_messages(AGENT_ID)
        assert store.get_read_index() == {}
        assert len(store.get_unread(agent_id=AGENT_ID)) == 1

    async def test_per_agent(self, mock_room):
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2))
        assert store.has_unread_messages(1) is True
        assert store.has_unread_messages(2) is False


class TestHasUnreadImmediateMessages:
    """has_unread_immediate_messages：语义是「有 immediate 消息还没被读」。"""

    def test_false_when_no_messages(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        assert store.has_unread_immediate_messages(AGENT_ID) is False

    async def test_false_for_regular_unread_messages(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2, insert_immediately=False))
        assert store.has_unread_immediate_messages(AGENT_ID) is False

    async def test_true_for_unread_immediate_message(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2, insert_immediately=True))
        assert store.has_unread_immediate_messages(AGENT_ID) is True

    async def test_false_after_read(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2, insert_immediately=True))
        store.get_unread(agent_id=AGENT_ID)
        assert store.has_unread_immediate_messages(AGENT_ID) is False

    async def test_false_for_own_immediate_message(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=AGENT_ID, insert_immediately=True))
        assert store.has_unread_immediate_messages(AGENT_ID) is False

    async def test_does_not_advance_cursor(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=2, insert_immediately=True))
        assert store.has_unread_immediate_messages(AGENT_ID) is True
        assert store.get_read_index() == {}


# --------------------------------------------------------------------------- #
# 4. 升级：只置标志位，不动 seq、不重排、不改变可见性
# --------------------------------------------------------------------------- #
class TestEscalateToImmediate:
    """escalate_to_immediate：只置 insert_immediately=True。"""

    async def test_sets_flag_and_preserves_seq(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        m = _msg(sender_id=OPERATOR_ID, content="hi", db_id=10)
        await store.append_and_assign_seq(m)

        result = store.escalate_to_immediate(db_id=10)

        assert result is m
        assert result.insert_immediately is True
        assert result.seq == 0

    async def test_does_not_remove_from_messages(self, mock_room):
        """升级不是「移入 pending 队列」：消息原地保留，可见性不变。"""
        store = RoomMessageStore(gt_room=mock_room)
        m = _msg(sender_id=OPERATOR_ID, db_id=10)
        await store.append_and_assign_seq(m)
        store.escalate_to_immediate(db_id=10)
        assert len(store.messages) == 1
        assert [x.seq for x in store.messages] == [0]

    async def test_does_not_reorder(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        m0 = _msg(sender_id=OPERATOR_ID, content="before", db_id=5)
        m1 = _msg(sender_id=OPERATOR_ID, content="target", db_id=10)
        m2 = _msg(sender_id=OPERATOR_ID, content="after", db_id=15)
        for m in (m0, m1, m2):
            await store.append_and_assign_seq(m)

        store.escalate_to_immediate(db_id=10)

        assert [m.id for m in store.messages] == [5, 10, 15]
        assert [m.seq for m in store.messages] == [0, 1, 2]

    async def test_escalated_message_still_delivered_in_order(self, mock_room):
        """升级后依然按原 seq 被读到，不会插队。"""
        store = RoomMessageStore(gt_room=mock_room)
        for db_id, content in ((5, "before"), (10, "target"), (15, "after")):
            await store.append_and_assign_seq(_msg(sender_id=OPERATOR_ID, content=content, db_id=db_id))
        store.escalate_to_immediate(db_id=10)
        assert [m.content for m in store.get_unread(agent_id=AGENT_ID)] == ["before", "target", "after"]

    async def test_raises_if_db_id_not_found(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        with pytest.raises(ValueError):
            store.escalate_to_immediate(db_id=999)

    async def test_raises_for_system_sender(self, mock_room):
        """SYSTEM 消息没有「立即引导」语义。"""
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=SYSTEM_ID, db_id=10))
        with pytest.raises(ValueError):
            store.escalate_to_immediate(db_id=10)

    async def test_raises_when_no_ai_recipient(self, mock_room):
        """AI 发给 Operator 的消息（房间内无 AI 接收方）不可升级。"""
        mock_room.agent_ids = [OPERATOR_ID, AGENT_ID]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=AGENT_ID, db_id=10))
        with pytest.raises(ValueError):
            store.escalate_to_immediate(db_id=10)

    async def test_agent_to_agent_unread_can_be_escalated(self, mock_room):
        """双 AI 单聊：A 发给 B 的未读普通消息可升级为加急。"""
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=1, content="plain", db_id=10))

        result = store.escalate_to_immediate(db_id=10)

        assert result.insert_immediately is True
        assert result.seq == 0

    async def test_agent_to_agent_raises_after_receiver_read(self, mock_room):
        """双 AI 单聊：接收方 B 已读后，A 那条消息不可再升级。"""
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=1, db_id=10))
        store.get_unread(agent_id=2)

        with pytest.raises(RuntimeError):
            store.escalate_to_immediate(db_id=10)

    async def test_raises_if_already_read_by_an_agent(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=OPERATOR_ID, db_id=10))
        store.get_unread(agent_id=AGENT_ID)

        with pytest.raises(RuntimeError):
            store.escalate_to_immediate(db_id=10)

    async def test_raises_if_any_of_multiple_agents_read(self, mock_room):
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=OPERATOR_ID, db_id=10))
        store.get_unread(agent_id=1)

        with pytest.raises(RuntimeError):
            store.escalate_to_immediate(db_id=10)

    async def test_succeeds_when_no_agent_has_read(self, mock_room):
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=OPERATOR_ID, db_id=10))
        assert store.escalate_to_immediate(db_id=10).insert_immediately is True

    async def test_is_idempotent(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=OPERATOR_ID, insert_immediately=True, db_id=10))
        store.escalate_to_immediate(db_id=10)
        result = store.escalate_to_immediate(db_id=10)
        assert result.insert_immediately is True
        assert result.seq == 0
        assert len(store.messages) == 1


# --------------------------------------------------------------------------- #
# 5. 旁路注入：inject 重建内存态，不写库、不广播
# --------------------------------------------------------------------------- #
class TestInject:
    """inject：从 DB 灌历史与读游标，显式排序并推导 _next_seq。"""

    def test_loads_messages(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        store.inject(messages=[_msg(content="a"), _msg(content="b")])
        assert [m.content for m in store.messages] == ["a", "b"]

    def test_sorts_by_seq(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        a, b, c = _msg(content="a"), _msg(content="b"), _msg(content="c")
        a.seq, b.seq, c.seq = 0, 1, 2
        store.inject(messages=[c, a, b])
        assert [m.content for m in store.messages] == ["a", "b", "c"]

    def test_next_seq_follows_max_seq(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        a, b = _msg(), _msg()
        a.seq, b.seq = 0, 1
        store.inject(messages=[a, b])
        assert store._next_seq == 2  # noqa: SLF001

    async def test_append_after_inject_continues_seq(self, mock_room):
        """重启后新消息不能撞号，也不能跳号。"""
        store = RoomMessageStore(gt_room=mock_room)
        a, b = _msg(), _msg()
        a.seq, b.seq = 0, 1
        store.inject(messages=[a, b])
        m = _msg(content="new")
        await store.append_and_assign_seq(m)
        assert m.seq == 2

    def test_next_seq_skips_null_seq_messages(self, mock_room):
        """历史 NULL seq 不影响起号，靠 DB 迁移补齐；此处只保证不因 NULL 归零。"""
        store = RoomMessageStore(gt_room=mock_room)
        legacy, ok = _msg(content="legacy"), _msg(content="ok")
        ok.seq = 7
        store.inject(messages=[legacy, ok])
        assert store._next_seq == 8  # noqa: SLF001

    def test_empty_messages_keeps_next_seq_zero(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        store.inject(messages=[])
        assert store._next_seq == 0  # noqa: SLF001

    def test_restores_read_index_with_int_keys(self, mock_room):
        """WS/DB 传回的 key 是字符串，需转回 int。"""
        store = RoomMessageStore(gt_room=mock_room)
        store.inject(messages=[], agent_read_index={"1": 3})
        assert store.get_read_index() == {1: 3}

    def test_ignores_invalid_read_index_keys(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        store.inject(messages=[], agent_read_index={"abc": 1, "2": 5})
        assert store.get_read_index() == {2: 5}

    def test_restored_read_index_affects_unread(self, mock_room):
        store = RoomMessageStore(gt_room=mock_room)
        a, b = _msg(content="a"), _msg(content="b")
        a.seq, b.seq = 0, 1
        store.inject(messages=[a, b], agent_read_index={"1": 1})
        assert [m.content for m in store.get_unread(agent_id=AGENT_ID)] == ["b"]

    def test_inject_does_not_write_db_or_publish(self, mock_room, mock_db_manager, mock_message_bus):
        store = RoomMessageStore(gt_room=mock_room)
        a = _msg()
        a.seq = 0
        store.inject(messages=[a])
        mock_db_manager.append_room_message.assert_not_awaited()
        mock_message_bus.publish.assert_not_called()


# --------------------------------------------------------------------------- #
# 6. mark_all_read
# --------------------------------------------------------------------------- #
class TestMarkAllRead:
    def test_sets_all_agents_cursor_to_next_seq(self, mock_room):
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        store.inject(messages=[_msg()])
        store._messages[0].seq = 0  # noqa: SLF001
        store._next_seq = 1  # noqa: SLF001
        store.mark_all_read()
        assert store.get_read_index() == {1: 1, 2: 1}

    async def test_clears_unread(self, mock_room):
        mock_room.agent_ids = [1, 2]
        store = RoomMessageStore(gt_room=mock_room)
        await store.append_and_assign_seq(_msg(sender_id=3, insert_immediately=True))
        store.mark_all_read()
        assert store.has_unread_messages(1) is False
        assert store.has_unread_immediate_messages(1) is False
        assert store.has_unread_messages(2) is False
