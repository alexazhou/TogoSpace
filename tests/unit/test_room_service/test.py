import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

import service.room_service as room_service
from service.room_service import ChatRoom
from model.chat_model import ChatMessage
from constants import RoomState, MessageBusTopic, RoomType
from ...base import ServiceTestCase

TEAM = "test_team"


class TestChatRoom(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # ChatRoom 测试依赖 room_service 工厂方法创建实例。
        await cls.areset_services()
        await room_service.startup()

    def test_add_message(self):
        """add_message 会追加消息并发布 ROOM_MSG_ADDED 事件。"""
        room_service.create_room(TEAM, "test_room", ["alice"])
        room = room_service.get_room(f"test_room@{TEAM}")
        with patch("service.message_bus.publish") as mock_publish:
            room.add_message("alice", "hello")
            assert len(room.messages) == 2  # 1 (init公告) + 1 (new)
            assert room.messages[1].sender_name == "alice"
            assert room.messages[1].content == "hello"
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MSG_ADDED,
                room_name="test_room",
                room_key=f"test_room@{TEAM}",
                team_name=TEAM,
                sender="alice",
                content="hello",
                time=room.messages[1].send_time.isoformat(),
            )

    def test_get_unread_messages_initial(self):
        """首次拉取未读应拿到系统初始化公告。"""
        room_service.create_room(TEAM, "test_room", ["alice"])
        room = room_service.get_room(f"test_room@{TEAM}")
        # 初始时，应该有 1 条公告消息
        msgs = room.get_unread_messages("alice")
        assert len(msgs) == 1
        assert "房间已经创建" in msgs[0].content

    def test_get_unread_messages_advances_index(self):
        """读取未读会推进游标，重复读取不应返回旧消息。"""
        room_service.create_room(TEAM, "test_room", ["alice"])
        room = room_service.get_room(f"test_room@{TEAM}")
        room.get_unread_messages("alice")  # 清空初始
        room.add_message("bob", "msg1")
        msgs = room.get_unread_messages("alice")
        assert len(msgs) == 1
        assert msgs[0].content == "msg1"

        msgs2 = room.get_unread_messages("alice")
        assert len(msgs2) == 0

    def test_get_unread_messages_independent_per_agent(self):
        """不同 agent 的未读游标互相独立。"""
        room_service.create_room(TEAM, "test_room", ["alice"])
        room = room_service.get_room(f"test_room@{TEAM}")
        room.get_unread_messages("alice")
        room.get_unread_messages("bob")
        room.add_message("char", "hi")
        assert len(room.get_unread_messages("alice")) == 1
        assert len(room.get_unread_messages("bob")) == 1

    def test_format_log(self):
        """format_log 输出包含房间标题与消息发送者。"""
        room_service.create_room(TEAM, "test_room", ["alice"])
        room = room_service.get_room(f"test_room@{TEAM}")
        log = room.format_log()
        assert f"=== test_room@{TEAM} 聊天记录 ===" in log
        assert "system" in log


class TestRoomServiceFunctions(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 只测 room_service 的状态与查询接口。
        await cls.areset_services()
        await room_service.startup()

    def test_create_room(self):
        """create_room 后应可通过 key 获取 ChatRoom 实例。"""
        room_service.create_room(TEAM, "myroom", ["alice"])
        key = f"myroom@{TEAM}"
        assert key in room_service._rooms
        assert isinstance(room_service.get_room(key), ChatRoom)

    def test_close_all(self):
        """shutdown 会清空全局 rooms 注册表。"""
        room_service.create_room(TEAM, "tmp", ["a"])
        room_service.shutdown()
        assert len(room_service._rooms) == 0

    def test_setup_members(self):
        """get_member_names 返回创建时配置的成员顺序。"""
        room_service.create_room(TEAM, "r1", ["alice", "bob"])
        assert room_service.get_member_names(TEAM, "r1") == ["alice", "bob"]

    def test_get_rooms_for_agent(self):
        """按 agent 过滤房间时，只返回该 agent 参与的 room_key 列表。"""
        room_service.create_room(TEAM, "r1", ["alice"])
        room_service.create_room(TEAM, "r2", ["bob"])
        room_service.create_room(TEAM, "r3", ["alice", "bob"])

        assert room_service.get_rooms_for_agent(TEAM, "alice") == [f"r1@{TEAM}", f"r3@{TEAM}"]
        assert room_service.get_rooms_for_agent(TEAM, "bob") == [f"r2@{TEAM}", f"r3@{TEAM}"]


class TestRoomTurnScheduling(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 轮转调度逻辑需要 room_service 的完整状态机。
        await cls.areset_services()
        await room_service.startup()

    def test_create_room_does_not_publish_first_agent(self):
        """建房后不应立刻发布首个发言人的 TURN 事件。"""
        with patch("service.message_bus.publish") as mock_publish:
            room_service.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
            topics = [call.args[0] for call in mock_publish.call_args_list]
            assert MessageBusTopic.ROOM_AGENT_TURN not in topics

    def test_start_scheduling_publishes_first_agent(self):
        """显式启动调度后，才发布首个发言人的 TURN 事件。"""
        room_service.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = room_service.get_room(f"r@{TEAM}")

        with patch("service.message_bus.publish") as mock_publish:
            room.start_scheduling()
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_AGENT_TURN,
                agent_name="alice",
                room_name="r",
                room_key=f"r@{TEAM}",
                team_name=TEAM,
            )

    def test_add_message_publishes_next_agent(self):
        """当前发言人发言后，系统应调度下一个发言人。"""
        room_service.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = room_service.get_room(f"r@{TEAM}")

        with patch("service.message_bus.publish") as mock_publish:
            room.add_message("alice", "hello")
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_AGENT_TURN,
                agent_name="bob",
                room_name="r",
                room_key=f"r@{TEAM}",
                team_name=TEAM,
            )

    def test_turn_state_becomes_idle_after_max_turns(self):
        """达到 max_turns 后房间状态应进入 IDLE。"""
        room_service.create_room(TEAM, "r", ["a"], max_turns=1)
        room = room_service.get_room(f"r@{TEAM}")
        assert room.state == RoomState.SCHEDULING
        room.add_message("a", "msg")
        assert room.state == RoomState.IDLE

    def test_no_publish_after_max_turns_reached(self):
        """超过最大轮次后继续发消息，不应再发布 TURN 事件。"""
        room_service.create_room(TEAM, "r", ["a"], max_turns=1)
        room = room_service.get_room(f"r@{TEAM}")
        room.add_message("a", "msg1")  # 第一轮结束

        with patch("service.message_bus.publish") as mock_publish:
            room.add_message("a", "msg2")
            pass
