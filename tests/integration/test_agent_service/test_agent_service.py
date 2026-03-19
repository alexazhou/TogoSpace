"""integration tests for core behavior in service.agent_service"""
import json
import os

from service import agent_service, room_service
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


class _AgentServiceCase(ServiceTestCase):
    """AgentService 集成测试基类：统一加载测试专用 agent/team 配置。"""

    @classmethod
    async def async_setup_class(cls):
        await cls.areset_services()
        await room_service.startup()
        agents_cfg = json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())
        team_cfg = json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read())
        await agent_service.startup()
        agent_service.load_agent_config(agents_cfg)
        await agent_service.create_team_agents([team_cfg])


class TestAgentServiceCreateTeamAgents(_AgentServiceCase):
    async def test_create_team_agents(self):
        """create_team_agents 后，team 维度的 agent 实例应全部可检索。"""
        assert agent_service.get_agent(TEAM, "alice") is not None
        assert agent_service.get_agent(TEAM, "bob") is not None


class TestAgentServiceGetAgentsInRoom(_AgentServiceCase):
    async def test_get_agents_in_room(self):
        """get_agents 只返回房间成员，并保持成员集合正确。"""
        await room_service.create_room(TEAM, "general", ["alice", "bob"])
        assert {a.name for a in agent_service.get_agents(TEAM, "general")} == {"alice", "bob"}


class TestAgentServiceGetAllRooms(_AgentServiceCase):
    async def test_get_all_rooms_for_agent(self):
        """get_all_rooms 应返回某个 agent 所在的所有 room_key。"""
        await room_service.create_room(TEAM, "general", ["alice"])
        assert f"general@{TEAM}" in agent_service.get_all_rooms(TEAM, "alice")


class TestAgentServiceSyncRoomMessages(_AgentServiceCase):
    async def test_sync_room_messages(self):
        """_sync_room_messages 会把房间中的新增消息同步进 agent 历史。"""
        await room_service.create_room(TEAM, "general", ["alice"])
        room = room_service.get_room(f"general@{TEAM}")
        await room.add_message("bob", "hello alice")

        alice = agent_service.get_agent(TEAM, "alice")
        await alice._sync_room_messages(room, with_prompt_lines=False)

        # 初始公告 + bob 消息
        assert len(alice._history) == 2
        assert "hello alice" in alice._history[1].content


class TestAgentServiceSyncSkipsOwnMessages(_AgentServiceCase):
    async def test_sync_room_skips_own_messages(self):
        """同步时应过滤 agent 自己发过的消息，避免历史自回灌。"""
        await room_service.create_room(TEAM, "general", ["alice"])
        room = room_service.get_room(f"general@{TEAM}")

        alice = agent_service.get_agent(TEAM, "alice")
        await room.add_message("alice", "i am talking")

        await alice._sync_room_messages(room, with_prompt_lines=False)
        # 只应有初始公告，不应有自己的消息
        assert len(alice._history) == 1
        assert "talking" not in alice._history[0].content
