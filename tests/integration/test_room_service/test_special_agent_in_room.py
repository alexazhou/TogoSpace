"""测试 ChatRoom._agents 是否包含 SpecialAgent。"""
import os
import sys

import pytest

from constants import RoomType, SpecialAgent
from dal.db import gtTeamManager, gtAgentManager, gtRoomManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import ormService, persistenceService, roomService, agentService
from tests.base import ServiceTestCase

TEAM = "test_special_agent_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestRoomContainsSpecialAgent(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()  # 确保 SpecialAgent 记录存在
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [GtAgent(team_id=team.id, name="alice", role_template_id=0)],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await agentService.shutdown()
        await ormService.shutdown()

    async def test_room_agents_contains_operator(self):
        """ChatRoom._agents 应包含 OPERATOR SpecialAgent。"""
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        assert alice is not None

        # 创建包含 Operator 的房间
        agent_ids = [int(SpecialAgent.OPERATOR.value), alice.id]
        gt_room = GtRoom(
            team_id=self.team_id,
            name="op_room",
            type=RoomType.GROUP,
            agent_ids=agent_ids,
            max_turns=1,
        )
        await gtRoomManager.save_room(gt_room)
        await roomService.load_team_rooms(self.team_id)

        room = roomService.get_room_by_key(f"op_room@{TEAM}")

        # 验证 _agents 包含 OPERATOR
        agent_names = [a.name for a in room._agents]
        assert "OPERATOR" in agent_names
        assert "alice" in agent_names

        # 验证 OPERATOR 的属性
        op_agent = next(a for a in room._agents if a.id == int(SpecialAgent.OPERATOR.value))
        assert op_agent.id == -1
        assert op_agent.team_id == -1  # 跨团队概念
        assert op_agent.name == "OPERATOR"
        assert op_agent.i18n == {"display_name": {"zh-CN": "OPERATOR", "en": "OPERATOR"}}

    async def test_room_agents_contains_system(self):
        """ChatRoom._agents 应包含 SYSTEM SpecialAgent（如果房间配置包含）。"""
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        assert alice is not None

        agent_ids = [int(SpecialAgent.SYSTEM.value), alice.id]
        gt_room = GtRoom(
            team_id=self.team_id,
            name="sys_room",
            type=RoomType.GROUP,
            agent_ids=agent_ids,
            max_turns=1,
        )
        await gtRoomManager.save_room(gt_room)
        await roomService.load_team_rooms(self.team_id)

        room = roomService.get_room_by_key(f"sys_room@{TEAM}")

        agent_names = [a.name for a in room._agents]
        assert "SYSTEM" in agent_names

        sys_agent = next(a for a in room._agents if a.id == int(SpecialAgent.SYSTEM.value))
        assert sys_agent.id == -2
        assert sys_agent.team_id == -1  # 跨团队概念
        assert sys_agent.name == "SYSTEM"
        assert sys_agent.i18n == {"display_name": {"zh-CN": "SYSTEM", "en": "SYSTEM"}}

    async def test_get_agent_ids_filters_system_by_default(self):
        """get_agent_ids() 默认不包含 SYSTEM，get_all_agent_ids() 包含。"""
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        assert alice is not None

        agent_ids = [int(SpecialAgent.SYSTEM.value), alice.id]
        gt_room = GtRoom(
            team_id=self.team_id,
            name="filter_room",
            type=RoomType.GROUP,
            agent_ids=agent_ids,
            max_turns=1,
        )
        await gtRoomManager.save_room(gt_room)
        await roomService.load_team_rooms(self.team_id)

        room = roomService.get_room_by_key(f"filter_room@{TEAM}")

        # get_agent_ids() 不包含 SYSTEM
        default_ids = room.get_agent_ids()
        assert int(SpecialAgent.SYSTEM.value) not in default_ids
        assert alice.id in default_ids

        # get_agent_ids(include_system=True) 包含 SYSTEM
        all_ids = room.get_agent_ids(include_system=True)
        assert int(SpecialAgent.SYSTEM.value) in all_ids
        assert alice.id in all_ids