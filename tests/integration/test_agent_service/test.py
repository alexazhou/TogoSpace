"""integration tests for service.agent_service"""
import json
import os
import pytest
from model.chat_model import ChatMessage
from service import agent_service, room_service
from service.agent_service import Agent
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


class TestSdkDoSend(ServiceTestCase):
    """测试 Agent._sdk_do_send：当前房间 vs 跨房间发言的路由与 done 标记行为。"""

    def setup_method(self):
        super().setup_method()
        room_service.startup()

    def _make_agent_with_slots(self, agent_name: str, current_room_name: str):
        """创建房间并为 agent 注入 SDK slots，模拟 _run_turn_sdk 执行前的状态。"""
        room_service.create_room(TEAM, current_room_name, [agent_name])
        room = room_service.get_room(f"{current_room_name}@{TEAM}")
        agent = Agent(name=agent_name, team_name=TEAM, system_prompt="test", model="test-model")
        agent._sdk_room_slot = [room]
        agent._sdk_done_slot = [False]
        return agent, room

    # ------------------------------------------------------------------
    # 发到当前房间
    # ------------------------------------------------------------------

    def test_send_to_current_room_sets_done(self):
        """发到当前房间后，_sdk_done_slot 应被标记为 True。"""
        alice, room = self._make_agent_with_slots("alice", "lobby")
        alice._sdk_do_send("lobby", "hi everyone")
        assert alice._sdk_done_slot[0] is True

    def test_send_to_current_room_message_appears(self):
        """发到当前房间的消息应出现在该房间里。"""
        alice, room = self._make_agent_with_slots("alice", "lobby")
        alice._sdk_do_send("lobby", "hi everyone")
        assert any(m.content == "hi everyone" for m in room.messages)

    def test_send_to_current_room_result_says_done(self):
        """发到当前房间时，返回的 tool result 应包含 '本轮发言结束' 字样。"""
        alice, room = self._make_agent_with_slots("alice", "lobby")
        result = alice._sdk_do_send("lobby", "hi")
        text = result["content"][0]["text"]
        assert "本轮发言结束" in text

    # ------------------------------------------------------------------
    # 跨房间发言
    # ------------------------------------------------------------------

    def test_send_cross_room_does_not_set_done(self):
        """发到其他房间时，_sdk_done_slot 不应被标记，当前轮次仍需继续。"""
        alice, current_room = self._make_agent_with_slots("alice", "private")
        room_service.create_room(TEAM, "group", ["alice"])
        alice._sdk_do_send("group", "hello group")
        assert alice._sdk_done_slot[0] is False

    def test_send_cross_room_lands_in_target(self):
        """跨房间消息应出现在目标房间，而非当前房间。"""
        alice, current_room = self._make_agent_with_slots("alice", "private")
        room_service.create_room(TEAM, "group", ["alice"])
        group = room_service.get_room(f"group@{TEAM}")
        alice._sdk_do_send("group", "hello group")
        assert any(m.content == "hello group" for m in group.messages)
        assert not any(m.content == "hello group" for m in current_room.messages)

    def test_send_cross_room_result_prompts_to_reply_current(self):
        """跨房间发言后，tool result 应提示 agent 还需回复当前房间。"""
        alice, current_room = self._make_agent_with_slots("alice", "private")
        room_service.create_room(TEAM, "group", ["alice"])
        result = alice._sdk_do_send("group", "hi")
        text = result["content"][0]["text"]
        assert current_room.name in text
        assert "本轮发言结束" not in text
