"""integration tests for service.agent_service"""
import json
import os
import pytest
from model.chat_model import ChatMessage
from service import agent_service, room_service
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


class TestAgentService(ServiceTestCase):
    def setup_method(self):
        super().setup_method()
        room_service.startup()
        agents_cfg = json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())
        team_cfg   = json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read())
        agent_service.startup()
        agent_service.load_agent_config(agents_cfg)
        agent_service.create_team_agents([team_cfg])

    def test_create_team_agents(self):
        assert agent_service.get_agent(TEAM, "alice") is not None
        assert agent_service.get_agent(TEAM, "bob") is not None

    def test_get_agents_in_room(self):
        room_service.create_room(TEAM, "general", ["alice", "bob"])
        assert {a.name for a in agent_service.get_agents(TEAM, "general")} == {"alice", "bob"}

    def test_get_all_rooms_for_agent(self):
        room_service.create_room(TEAM, "general", ["alice"])
        assert f"general@{TEAM}" in agent_service.get_all_rooms(TEAM, "alice")

    def test_sync_room_messages(self):
        room_service.create_room(TEAM, "general", ["alice"])
        room = room_service.get_room(f"general@{TEAM}")
        room.add_message("bob", "hello alice")

        alice = agent_service.get_agent(TEAM, "alice")
        alice.sync_room(room)

        # 初始公告 + bob 消息
        assert len(alice._history) == 2
        assert "hello alice" in alice._history[1].content

    def test_sync_room_skips_own_messages(self):
        room_service.create_room(TEAM, "general", ["alice"])
        room = room_service.get_room(f"general@{TEAM}")

        alice = agent_service.get_agent(TEAM, "alice")
        # alice 发送消息
        room.add_message("alice", "i am talking")

        alice.sync_room(room)
        # 只应有初始公告，不应有自己的消息
        assert len(alice._history) == 1
        assert "talking" not in alice._history[0].content
