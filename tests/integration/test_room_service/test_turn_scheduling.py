import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import MessageBusTopic, RoomState
from dal.db import gtTeamManager, gtAgentManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestTurnScheduling(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()

        # 预创建 team，_create_room 不再自动创建
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
                GtAgent(team_id=team.id, name="a", role_template_id=0),
            ],
        )

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_create_room_does_not_publish_first_agent(self):
        """建房后不应立刻发布首个发言人的 TURN 事件（INIT 状态不广播状态变更）。"""
        with patch("service.messageBus.publish") as mock_publish:
            await roomService.ensure_room_record(TEAM, "r", ["alice", "bob"], max_turns=5)
            topics = [call.args[0] for call in mock_publish.call_args_list]
            assert MessageBusTopic.ROOM_STATUS_CHANGED not in topics

    async def test_start_scheduling_publishes_first_agent(self):
        """显式启动调度后，才发布首个发言人的状态变更事件。"""
        await roomService.ensure_room_record(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        alice_id = room.get_agent_id_by_name("alice")

        with patch("service.messageBus.publish") as mock_publish:
            await room.activate_scheduling()
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_STATUS_CHANGED,
                gt_room=room.gt_room,
                state=RoomState.SCHEDULING,
                current_turn_agent=room.get_gt_agent(alice_id),
                need_scheduling=True,
            )

    async def test_add_message_publishes_next_agent(self):
        """当前发言人发言后，调用 finish_turn 才调度下一个发言人。"""
        await roomService.ensure_room_record(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        await room.activate_scheduling()
        bob_id = room.get_agent_id_by_name("bob")

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(room.get_agent_id_by_name("alice"), "hello")
            # 消息不会自动推进轮次，需要显式调用 finish_turn
            await room.finish_turn(room.get_agent_id_by_name("alice"))
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_STATUS_CHANGED,
                gt_room=room.gt_room,
                state=RoomState.SCHEDULING,
                current_turn_agent=room.get_gt_agent(bob_id),
                need_scheduling=True,
            )

    async def test_turn_state_becomes_idle_after_max_turns(self):
        """房间默认 INIT，完成一轮后应进入 IDLE。"""
        await roomService.ensure_room_record(TEAM, "r", ["a"], max_turns=1)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        assert room.state == RoomState.INIT
        await room.activate_scheduling()
        a_id = room.get_agent_id_by_name("a")
        await room.add_message(a_id, "msg")
        # 消息不会自动推进轮次，需要显式调用 finish_turn
        await room.finish_turn(a_id)
        assert room.state == RoomState.IDLE

    async def test_no_publish_after_max_turns_reached(self):
        """超过最大轮次后继续发消息，不应再触发新的调度。"""
        await roomService.ensure_room_record(TEAM, "r", ["a"], max_turns=1)
        room = roomService.get_room_by_key(f"r@{TEAM}")
        await room.activate_scheduling()
        a_id = room.get_agent_id_by_name("a")
        await room.add_message(a_id, "msg1")

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(a_id, "msg2")
            scheduling_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert scheduling_calls == []
