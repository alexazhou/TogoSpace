import os
import sys
from unittest.mock import patch

import pytest

import service.room_service as room_service
from constants import MessageBusTopic, RoomState
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestTurnScheduling(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await room_service.startup()

    async def test_create_room_does_not_publish_first_agent(self):
        """建房后不应立刻发布首个发言人的 TURN 事件。"""
        with patch("service.message_bus.publish") as mock_publish:
            await room_service.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
            topics = [call.args[0] for call in mock_publish.call_args_list]
            assert MessageBusTopic.ROOM_AGENT_TURN not in topics

    async def test_start_scheduling_publishes_first_agent(self):
        """显式启动调度后，才发布首个发言人的 TURN 事件。"""
        await room_service.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
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

    async def test_add_message_publishes_next_agent(self):
        """当前发言人发言后，系统应调度下一个发言人。"""
        await room_service.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = room_service.get_room(f"r@{TEAM}")

        with patch("service.message_bus.publish") as mock_publish:
            await room.add_message("alice", "hello")
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_AGENT_TURN,
                agent_name="bob",
                room_name="r",
                room_key=f"r@{TEAM}",
                team_name=TEAM,
            )

    async def test_turn_state_becomes_idle_after_max_turns(self):
        """达到 max_turns 后房间状态应进入 IDLE。"""
        await room_service.create_room(TEAM, "r", ["a"], max_turns=1)
        room = room_service.get_room(f"r@{TEAM}")
        assert room.state == RoomState.SCHEDULING
        await room.add_message("a", "msg")
        assert room.state == RoomState.IDLE

    async def test_no_publish_after_max_turns_reached(self):
        """超过最大轮次后继续发消息，不应再发布 TURN 事件。"""
        await room_service.create_room(TEAM, "r", ["a"], max_turns=1)
        room = room_service.get_room(f"r@{TEAM}")
        await room.add_message("a", "msg1")

        with patch("service.message_bus.publish") as mock_publish:
            await room.add_message("a", "msg2")
            assert MessageBusTopic.ROOM_AGENT_TURN not in [call.args[0] for call in mock_publish.call_args_list]
