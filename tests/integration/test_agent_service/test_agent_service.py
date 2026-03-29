"""integration tests for core behavior in service.agentService"""
import os
import sys

import pytest

from dal.db import gtTeamManager
from service import roleTemplateService, agentService, roomService, ormService, persistenceService
from util import configUtil
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class _agentServiceCase(ServiceTestCase):
    """agentService 集成测试基类：统一加载测试专用 agent/team 配置。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls.TEST_DB_PATH
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        cfg = configUtil.load(_CONFIG_DIR, force_reload=True)
        team_cfg = cfg.teams[0]
        await gtTeamManager.import_team_from_config(team_cfg)
        await roleTemplateService.startup()
        await agentService.startup()
        await agentService.load_team_ids([team_cfg])
        await agentService.create_team_agents([team_cfg])

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()


class TestagentServiceCreateTeamAgents(_agentServiceCase):
    async def test_create_team_members(self):
        """create_team_members 后，team 维度的 agent 实例应全部可检索。"""
        assert agentService.get_team_agent(TEAM, "alice") is not None
        assert agentService.get_team_agent(TEAM, "bob") is not None


class TestagentServiceGetAgentsInRoom(_agentServiceCase):
    async def test_get_agents_in_room(self):
        """get_agents 只返回房间成员，并保持成员集合正确。"""
        await roomService.create_room(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert {a.name for a in agentService.get_team_agents(room.room_id)} == {"alice", "bob"}


class TestAgentServiceGetInfo(_agentServiceCase):
    async def test_get_info(self):
        """get_info 应返回面向 Web 层的标准 Agent 信息。"""
        alice = agentService.get_team_agent(TEAM, "alice")

        info = alice.get_info()

        assert info.name == "alice"
        assert info.template_name == "alice"
        assert info.team_name == TEAM
        assert info.status.name == "IDLE"


class TestagentServiceGetAllRooms(_agentServiceCase):
    async def test_get_all_rooms_for_agent(self):
        """get_all_rooms 应返回某个 agent 所在的所有 room_id。"""
        await roomService.create_room(TEAM, "general", ["alice"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert room.room_id in agentService.get_all_rooms(TEAM, "alice")


class TestagentServiceSyncRoomMessages(_agentServiceCase):
    async def test_sync_room_messages(self):
        """_sync_room_messages 会把房间中的新增消息同步进 agent 历史。"""
        await roomService.create_room(TEAM, "general", ["alice"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        await room.add_message("bob", "hello alice")

        alice = agentService.get_team_agent(TEAM, "alice")
        synced_count = await alice.sync_room_messages(room)

        # 初始公告 + bob 消息
        assert synced_count == 2
        assert len(alice._history) == 2
        assert "hello alice" in alice._history[1].content


class TestagentServiceSyncSkipsOwnMessages(_agentServiceCase):
    async def test_sync_room_skips_own_messages(self):
        """同步时应过滤 agent 自己发过的消息，避免历史自回灌。"""
        await roomService.create_room(TEAM, "general", ["alice"])
        room = roomService.get_room_by_key(f"general@{TEAM}")

        alice = agentService.get_team_agent(TEAM, "alice")
        await room.add_message("alice", "i am talking")

        synced_count = await alice.sync_room_messages(room)
        # 只应有初始公告，不应有自己的消息
        assert synced_count == 1
        assert len(alice._history) == 1
        assert "talking" not in alice._history[0].content
