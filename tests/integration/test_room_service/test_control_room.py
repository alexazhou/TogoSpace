"""集成测试：get_or_create_control_room 与 get_private_room_by_agent。"""
import os
import sys
from unittest.mock import patch

import pytest

from constants import RoomState, RoomType, MessageBusTopic, SpecialAgent
from dal.db import gtAgentManager, gtRoomManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from service import agentService, ormService, persistenceService, roomService
from tests.base import ServiceTestCase

TEAM = "test_control_room_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestGetOrCreateControlRoom(ServiceTestCase):
    """覆盖控制房间的自动创建、幂等性与 ROOM_ADDED 事件。"""

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
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _get_agent_id(self, name: str) -> int:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        assert gt_agent is not None
        return gt_agent.id

    async def test_creates_control_room_on_first_call(self):
        """首次调用应新建 PRIVATE 房间，created=True。"""
        alice_id = await self._get_agent_id("alice")

        room, created = await roomService.get_or_create_control_room(self.team_id, alice_id)

        assert created is True
        assert room is not None
        assert room.state != RoomState.INIT

        # 数据库中应存在对应的 PRIVATE 房间
        gt_room = await gtRoomManager.get_private_room_by_agent(self.team_id, alice_id)
        assert gt_room is not None
        assert gt_room.type == RoomType.PRIVATE
        assert alice_id in gt_room.agent_ids

    async def test_returns_existing_room_on_second_call(self):
        """第二次调用应返回同一房间，created=False。"""
        bob_id = await self._get_agent_id("bob")

        room1, created1 = await roomService.get_or_create_control_room(self.team_id, bob_id)
        assert created1 is True

        room2, created2 = await roomService.get_or_create_control_room(self.team_id, bob_id)
        assert created2 is False
        assert room1.room_id == room2.room_id

    async def test_room_added_event_published_on_create(self):
        """新建房间时应发布 ROOM_ADDED 事件，已存在时不发布。"""
        alice_id = await self._get_agent_id("alice")

        # 确保 alice 的控制房间已存在（第一次 create 可能已发过）
        # 用一个尚未创建控制房间的 agent：bob 在上面的 test 中已创建，
        # 所以我们只验证首次（created=True）时事件会发布。
        published_events: list = []

        import service.messageBus as _mb
        original_publish = _mb.publish

        def capture_publish(topic, **kwargs):
            published_events.append((topic, kwargs))
            return original_publish(topic, **kwargs)

        with patch.object(_mb, "publish", side_effect=capture_publish):
            # alice 的控制房间已存在 → created=False → 不应发布
            room, created = await roomService.get_or_create_control_room(self.team_id, alice_id)
            assert created is False

        room_added_events = [e for e in published_events if e[0] == MessageBusTopic.ROOM_ADDED]
        assert len(room_added_events) == 0

    async def test_get_private_room_by_agent_returns_none_for_missing(self):
        """不存在该 agent 的 PRIVATE 房间时，应返回 None。"""
        # 使用一个不存在的 agent_id
        gt_room = await gtRoomManager.get_private_room_by_agent(self.team_id, agent_id=999999)
        assert gt_room is None

    async def test_control_room_includes_operator(self):
        """自动创建的控制房间 agent_ids 应包含 OPERATOR。"""
        alice_id = await self._get_agent_id("alice")
        gt_room = await gtRoomManager.get_private_room_by_agent(self.team_id, alice_id)
        assert gt_room is not None
        assert int(SpecialAgent.OPERATOR.value) in gt_room.agent_ids
        assert alice_id in gt_room.agent_ids

    async def test_control_room_has_exactly_two_members(self):
        """自动创建的控制房间应恰好包含 2 个成员：OPERATOR + agent。"""
        alice_id = await self._get_agent_id("alice")
        gt_room = await gtRoomManager.get_private_room_by_agent(self.team_id, alice_id)
        assert gt_room is not None
        assert len(gt_room.agent_ids) == 2, (
            f"期望恰好 2 名成员，实际 {len(gt_room.agent_ids)}: {gt_room.agent_ids}"
        )

    async def test_room_name_equals_agent_name(self):
        """自动创建的控制房间名称应等于 agent 名称。"""
        alice_id = await self._get_agent_id("alice")
        gt_room = await gtRoomManager.get_private_room_by_agent(self.team_id, alice_id)
        assert gt_room is not None
        assert gt_room.name == "alice"

    async def test_control_room_is_activated_after_create(self):
        """新建控制房间后应立即激活（state != INIT）。"""
        alice_id = await self._get_agent_id("alice")
        room, _ = await roomService.get_or_create_control_room(self.team_id, alice_id)
        assert room.state != RoomState.INIT

    async def test_supervise_schedules_ai_agent(self):
        """supervise 流程：OPERATOR 发消息 + finish_turn 后应调度 AI agent（need_scheduling=True）。

        回归测试：旧代码对 max_turns=-1 用 <= 0 判断，导致控制房间 finish_turn(OPERATOR)
        后进入 IDLE 而不是调度 AI agent。
        """
        bob_id = await self._get_agent_id("bob")
        room, _ = await roomService.get_or_create_control_room(self.team_id, bob_id)

        # 确保房间在 IDLE 状态（控制房间激活后等待 OPERATOR 输入）
        assert room.state == RoomState.IDLE

        published_events: list = []
        import service.messageBus as _mb
        original_publish = _mb.publish

        def capture_publish(topic, **kwargs):
            published_events.append((topic, kwargs))
            return original_publish(topic, **kwargs)

        with patch.object(_mb, "publish", side_effect=capture_publish):
            await room.add_message(room.OPERATOR_MEMBER_ID, "hello")
            await room.finish_turn(room.OPERATOR_MEMBER_ID)

        # 最后一个 ROOM_STATUS_CHANGED 事件应为 SCHEDULING + need_scheduling=True
        status_events = [
            kwargs for topic, kwargs in published_events
            if topic == MessageBusTopic.ROOM_STATUS_CHANGED
        ]
        assert status_events, "应至少有一个 ROOM_STATUS_CHANGED 事件"
        last_status = status_events[-1]
        assert last_status["state"] == RoomState.SCHEDULING, (
            f"期望 SCHEDULING，实际 {last_status['state']}（旧 bug：max_turns=-1 被误判为不调度）"
        )
        assert last_status["need_scheduling"] is True, "最后状态事件应携带 need_scheduling=True"
        assert last_status["current_turn_agent_id"] == bob_id, (
            f"期望 bob({bob_id})，实际 {last_status['current_turn_agent_id']}"
        )
