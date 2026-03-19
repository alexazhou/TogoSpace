"""integration tests for Agent._sdk_do_send routing behavior"""
from service import room_service
from service.agent_service import Agent

from ...base import ServiceTestCase

TEAM = "test_team"


class TestSdkDoSend(ServiceTestCase):
    """测试 Agent._sdk_do_send：当前房间 vs 跨房间发言的路由与 done 标记行为。"""

    @classmethod
    async def async_setup_class(cls):
        # 仅依赖 room_service，无需启动完整 agent/service 栈。
        await cls.areset_services()
        await room_service.startup()

    async def _make_agent_with_slots(self, agent_name: str, current_room_name: str):
        """创建房间并为 agent 注入 SDK slots，模拟 _run_turn_sdk 执行前的状态。"""
        await room_service.create_room(TEAM, current_room_name, [agent_name])
        room = room_service.get_room(f"{current_room_name}@{TEAM}")
        agent = Agent(name=agent_name, team_name=TEAM, system_prompt="test", model="test-model")
        agent.current_room = room
        return agent, room

    async def test_send_to_current_room_sets_done(self):
        """发到当前房间后，本轮应结束（current_room 置空）。"""
        alice, room = await self._make_agent_with_slots("alice", "lobby")
        await alice._sdk_do_send("lobby", "hi everyone")
        assert alice.current_room is None

    async def test_send_to_current_room_message_appears(self):
        """发到当前房间的消息应出现在该房间里。"""
        alice, room = await self._make_agent_with_slots("alice", "lobby")
        await alice._sdk_do_send("lobby", "hi everyone")
        assert any(m.content == "hi everyone" for m in room.messages)

    async def test_send_to_current_room_result_says_done(self):
        """发到当前房间时，返回的 tool result 应包含 '本轮发言结束' 字样。"""
        alice, room = await self._make_agent_with_slots("alice", "lobby")
        result = await alice._sdk_do_send("lobby", "hi")
        text = result["content"][0]["text"]
        assert "本轮发言结束" in text

    async def test_send_cross_room_does_not_set_done(self):
        """发到其他房间时，不应结束当前轮次。"""
        alice, current_room = await self._make_agent_with_slots("alice", "private")
        await room_service.create_room(TEAM, "group", ["alice"])
        await alice._sdk_do_send("group", "hello group")
        assert alice.current_room is current_room

    async def test_send_cross_room_lands_in_target(self):
        """跨房间消息应出现在目标房间，而非当前房间。"""
        alice, current_room = await self._make_agent_with_slots("alice", "private")
        await room_service.create_room(TEAM, "group", ["alice"])
        group = room_service.get_room(f"group@{TEAM}")
        await alice._sdk_do_send("group", "hello group")
        assert any(m.content == "hello group" for m in group.messages)
        assert not any(m.content == "hello group" for m in current_room.messages)

    async def test_send_cross_room_result_prompts_to_reply_current(self):
        """跨房间发言后，tool result 应提示 agent 还需回复当前房间。"""
        alice, current_room = await self._make_agent_with_slots("alice", "private")
        await room_service.create_room(TEAM, "group", ["alice"])
        result = await alice._sdk_do_send("group", "hi")
        text = result["content"][0]["text"]
        assert current_room.name in text
        assert "本轮发言结束" not in text
