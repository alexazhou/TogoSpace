"""unit tests for service.room_service"""
import pytest
from unittest.mock import patch, MagicMock

import service.room_service as room_service
import service.message_bus as message_bus
from service.room_service import ChatRoom
from constants import RoomState, MessageBusTopic


@pytest.fixture(autouse=True)
def clean():
    message_bus.init()
    room_service.close_all()
    yield
    room_service.close_all()
    message_bus.stop()


class TestChatRoom:
    def setup_method(self):
        room_service.init("test_room")
        self.room = room_service.get_room("test_room")

    # ---------- basic message ----------

    def test_add_message(self):
        self.room.add_message("alice", "你好")
        assert len(self.room.messages) == 1
        assert self.room.messages[0].sender_name == "alice"
        assert self.room.messages[0].content == "你好"

    def test_get_unread_messages_initial(self):
        self.room.add_message("alice", "hello")
        self.room.add_message("bob", "world")
        msgs = self.room.get_unread_messages("alice")
        assert len(msgs) == 2

    def test_get_unread_messages_advances_index(self):
        self.room.add_message("alice", "first")
        self.room.get_unread_messages("alice")
        self.room.add_message("bob", "second")
        msgs = self.room.get_unread_messages("alice")
        assert len(msgs) == 1
        assert msgs[0].content == "second"

    def test_get_unread_messages_independent_per_agent(self):
        self.room.add_message("alice", "msg")
        self.room.get_unread_messages("alice")   # alice reads
        msgs_bob = self.room.get_unread_messages("bob")  # bob hasn't read yet
        assert len(msgs_bob) == 1

    # ---------- format_log ----------

    def test_format_log(self):
        self.room.add_message("alice", "你好")
        self.room.add_message("bob", "世界")
        log = self.room.format_log()
        assert "test_room" in log
        assert "alice" in log and "你好" in log
        assert "bob" in log and "世界" in log


class TestRoomServiceFunctions:
    def test_init_creates_room(self):
        room_service.init("myroom")
        room = room_service.get_room("myroom")
        assert room.name == "myroom"

    def test_get_room_not_found_raises(self):
        with pytest.raises(RuntimeError):
            room_service.get_room("nonexistent")

    def test_close_removes_room(self):
        room_service.init("tmp")
        room_service.close("tmp")
        with pytest.raises(RuntimeError):
            room_service.get_room("tmp")

    def test_setup_members(self):
        room_service.init("r1")
        room_service.setup_members("r1", ["alice", "bob"])
        assert room_service.get_member_names("r1") == ["alice", "bob"]

    def test_get_rooms_for_agent(self):
        room_service.init("r1")
        room_service.init("r2")
        room_service.setup_members("r1", ["alice", "bob"])
        room_service.setup_members("r2", ["alice", "charlie"])
        rooms = room_service.get_rooms_for_agent("alice")
        assert set(rooms) == {"r1", "r2"}
        assert room_service.get_rooms_for_agent("bob") == ["r1"]


class TestRoomTurnScheduling:
    def test_setup_turns_publishes_first_agent(self):
        room_service.init("r")
        published = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: published.append(m.payload))
        room_service.get_room("r").setup_turns(["alice", "bob"], max_turns=2)
        assert len(published) == 1
        assert published[0]["agent_name"] == "alice"

    def test_add_message_publishes_next_agent(self):
        room_service.init("r")
        room = room_service.get_room("r")
        room.setup_turns(["alice", "bob"], max_turns=2)

        published = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: published.append(m.payload))
        room.add_message("alice", "hi")
        assert published[-1]["agent_name"] == "bob"

    def test_turn_state_becomes_idle_after_max_turns(self):
        room_service.init("r")
        room = room_service.get_room("r")
        room.setup_turns(["alice"], max_turns=1)
        assert room.state == RoomState.SCHEDULING
        room.add_message("alice", "done")
        assert room.state == RoomState.IDLE

    def test_no_publish_after_max_turns_reached(self):
        room_service.init("r")
        room = room_service.get_room("r")
        room.setup_turns(["alice"], max_turns=1)

        published = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: published.append(m.payload))
        room.add_message("alice", "done")
        assert len(published) == 0  # 已达上限，不再发布
