"""集成测试：Agent 单聊加急消息（立即注入）——房间查找与双 AI 尾唤醒。

对应设计文档 `docs/design/2026-11-05-agent-immediate-message/step2_technical.md` §6：
- `get_private_rooms_for_agent` 返回该 Agent 的所有已激活 PRIVATE 房间，Operator 控制房排首位；
- 双 AI 单聊 `[A, B]` 中 B 结束轮次切回 IDLE 后，尾唤醒应调度 B（接收方）而非 A（发送方）。
"""
import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import MessageBusTopic, RoomState, RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoomManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import agentService
from tests.base import ServiceTestCase

TEAM = "test_private_rooms_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestGetPrivateRoomsForAgent(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
                GtAgent(team_id=team.id, name="carol", role_template_id=0),
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _agent_id(self, name: str) -> int:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        assert gt_agent is not None
        return gt_agent.id

    async def _make_private_room(self, name: str, agent_ids: list[int], *, activate: bool = True) -> int:
        gt_room = await gtRoomManager.save_room(GtRoom(
            team_id=self.team_id,
            name=name,
            type=RoomType.PRIVATE,
            initial_topic="",
            max_rounds=-1,
            agent_ids=agent_ids,
        ))
        await roomService.load_all_rooms()
        if activate:
            room = roomService.get_room(gt_room.id)
            assert room is not None
            with patch("service.messageBus.publish"):
                await room.activate_scheduling()
        return gt_room.id

    async def test_returns_only_private_rooms_of_that_agent(self):
        alice_id = await self._agent_id("alice")
        bob_id = await self._agent_id("bob")
        carol_id = await self._agent_id("carol")

        # 群聊不应出现在结果里（先建群聊：create_room 会 load_all_rooms 重载，会把已激活房间状态重置）
        await self.create_room(TEAM, "group_room", ["alice", "bob", "carol"], room_type=RoomType.GROUP)
        room_id = await self._make_private_room("alice_bob", [alice_id, bob_id])

        rooms = roomService.get_private_rooms_for_agent(self.team_id, alice_id)

        assert [r.room_id for r in rooms] == [room_id]
        assert carol_id not in rooms[0]._agent_ids

    async def test_operator_control_room_comes_first(self):
        alice_id = await self._agent_id("alice")
        bob_id = await self._agent_id("bob")

        # 先建 agent-agent 房间，再建 Operator 控制房，确认排序不是靠插入顺序
        agent_room_id = await self._make_private_room("alice_bob_order", [alice_id, bob_id])
        control_room, _ = await roomService.get_or_create_control_room(self.team_id, alice_id)

        rooms = roomService.get_private_rooms_for_agent(self.team_id, alice_id)

        assert rooms[0].room_id == control_room.room_id
        assert agent_room_id in [r.room_id for r in rooms]
        assert rooms[0].OPERATOR_MEMBER_ID in rooms[0]._agent_ids

    async def test_excludes_init_state_rooms(self):
        bob_id = await self._agent_id("bob")
        carol_id = await self._agent_id("carol")

        room_id = await self._make_private_room("bob_carol_init", [bob_id, carol_id], activate=False)
        room = roomService.get_room(room_id)
        assert room is not None
        assert room.state == RoomState.INIT

        rooms = roomService.get_private_rooms_for_agent(self.team_id, bob_id)

        assert room_id not in [r.room_id for r in rooms]

    async def test_returns_empty_for_agent_without_private_room(self):
        carol_id = await self._agent_id("carol")
        # 先清掉 carol 相关私聊房间，确保该用例不依赖执行顺序
        for room in roomService.get_all_rooms():
            if room.team_id == self.team_id and carol_id in room._agent_ids and room.room_type == RoomType.PRIVATE:
                gt_room = await gtRoomManager.get_room_by_id(room.room_id)
                if gt_room is not None:
                    await gtRoomManager.delete_room(room.room_id)
        await roomService.load_all_rooms()

        assert roomService.get_private_rooms_for_agent(self.team_id, carol_id) == []


class TestDoubleAiTailWake(ServiceTestCase):
    """双 AI 单聊尾唤醒：B 在 `agent_ids[1]`，结束轮次后应被唤醒的是 B。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM + "_tail"))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _agent_id(self, name: str) -> int:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        assert gt_agent is not None
        return gt_agent.id

    async def _build_double_ai_idle_room(self, room_name: str):
        """建一个 `[A, B]` 双 AI 单聊，并跑到「两人都无话可说 → IDLE」的状态。"""
        alice_id = await self._agent_id("alice")
        bob_id = await self._agent_id("bob")
        gt_room = await gtRoomManager.save_room(GtRoom(
            team_id=self.team_id,
            name=room_name,
            type=RoomType.PRIVATE,
            initial_topic="",
            max_rounds=-1,
            agent_ids=[alice_id, bob_id],
        ))
        await roomService.load_all_rooms()
        room = roomService.get_room(gt_room.id)
        assert room is not None
        with patch("service.messageBus.publish"):
            await room.activate_scheduling()
            # A 无话可说 → 推进到 B；B 也无话可说 → 私聊所有 AI 均已跳过 → IDLE
            assert await room.handle_finish_request(alice_id) is True
            assert await room.handle_finish_request(bob_id) is True
        assert room.state == RoomState.IDLE
        return room, alice_id, bob_id

    async def test_tail_wake_targets_receiver_not_sender(self):
        """尾唤醒传「另一成员 A」时，`current_turn_agent_id` 是接收方 B，不是发送方 A。"""
        room, alice_id, bob_id = await self._build_double_ai_idle_room("tail_a_b")

        published: list = []
        import service.messageBus as _mb
        original_publish = _mb.publish

        def capture_publish(topic, **kwargs):
            published.append((topic, kwargs))
            return original_publish(topic, **kwargs)

        with patch.object(_mb, "publish", side_effect=capture_publish):
            # 等价于 _wake_next_turn_if_unread(room)：触发者传房间内「另一成员」A
            await room.handle_finish_request(alice_id)

        status_events = [kw for topic, kw in published if topic == MessageBusTopic.ROOM_STATUS_CHANGED]
        assert status_events, "尾唤醒应发布 ROOM_STATUS_CHANGED"
        assert status_events[-1]["current_turn_agent_id"] == bob_id, (
            f"期望接收方 B({bob_id})，实际 {status_events[-1]['current_turn_agent_id']}"
            f"（旧 bug：从 index=0 开始误唤醒发送方 A({alice_id})）"
        )

    async def test_operator_caller_still_starts_from_first_agent(self):
        """回归：触发者是 OPERATOR 时行为不变，仍按原语义不记 `_last_speaker_id`。"""
        room, alice_id, bob_id = await self._build_double_ai_idle_room("tail_op")

        published: list = []
        import service.messageBus as _mb
        original_publish = _mb.publish

        def capture_publish(topic, **kwargs):
            published.append((topic, kwargs))
            return original_publish(topic, **kwargs)

        with patch.object(_mb, "publish", side_effect=capture_publish):
            await room.handle_finish_request(room.OPERATOR_MEMBER_ID)

        status_events = [kw for topic, kw in published if topic == MessageBusTopic.ROOM_STATUS_CHANGED]
        assert status_events
        assert status_events[-1]["current_turn_agent_id"] == alice_id
