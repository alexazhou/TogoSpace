import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import MessageBusTopic, RoomState
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestTurnScheduling(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls.TEST_DB_PATH
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_create_room_does_not_publish_first_agent(self):
        """建房后不应立刻发布首个发言人的 TURN 事件。"""
        with patch("service.messageBus.publish") as mock_publish:
            await roomService.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
            topics = [call.args[0] for call in mock_publish.call_args_list]
            assert MessageBusTopic.ROOM_MEMBER_TURN not in topics

    async def test_start_scheduling_publishes_first_agent(self):
        """显式启动调度后，才发布首个发言人的 TURN 事件。"""
        await roomService.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = roomService.get_room_by_key(f"r@{TEAM}")

        with patch("service.messageBus.publish") as mock_publish:
            room.activate_scheduling()
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MEMBER_TURN,
                member_name="alice",
                room_id=room.room_id,
                room_name="r",
                room_key=f"r@{TEAM}",
                team_name=TEAM,
            )

    async def test_add_message_publishes_next_agent(self):
        """当前发言人发言后，调用 finish_turn 才调度下一个发言人。"""
        await roomService.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        room.activate_scheduling()

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("alice", "hello")
            # 消息不会自动推进轮次，需要显式调用 finish_turn
            room.finish_turn("alice")
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MEMBER_TURN,
                member_name="bob",
                room_id=room.room_id,
                room_name="r",
                room_key=f"r@{TEAM}",
                team_name=TEAM,
            )

    async def test_turn_state_becomes_idle_after_max_turns(self):
        """房间默认 INIT，完成一轮后应进入 IDLE。"""
        await roomService.create_room(TEAM, "r", ["a"], max_turns=1)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        assert room.state == RoomState.INIT
        room.activate_scheduling()
        await room.add_message("a", "msg")
        # 消息不会自动推进轮次，需要显式调用 finish_turn
        room.finish_turn("a")
        assert room.state == RoomState.IDLE

    async def test_no_publish_after_max_turns_reached(self):
        """超过最大轮次后继续发消息，不应再发布 TURN 事件。"""
        await roomService.create_room(TEAM, "r", ["a"], max_turns=1)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        room.activate_scheduling()
        await room.add_message("a", "msg1")

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("a", "msg2")
            assert MessageBusTopic.ROOM_MEMBER_TURN not in [call.args[0] for call in mock_publish.call_args_list]
