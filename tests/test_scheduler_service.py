import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from service.agent_service import Agent
from service.chat_room_service import ChatRoom
from service.scheduler_service import Scheduler


def make_agent(name="agent", reply="hello"):
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.generate_with_function_calling = AsyncMock(return_value=(reply, []))
    return agent


@pytest.fixture
def two_agents():
    return [make_agent("agent1", "reply1"), make_agent("agent2", "reply2")]


@pytest.fixture
def chat_room():
    return ChatRoom("test_room")


class TestScheduler:
    @pytest.mark.asyncio
    async def test_run_calls_each_agent_in_order(self, two_agents, chat_room):
        with patch("service.scheduler_service.build_tools", return_value=[]):
            scheduler = Scheduler(two_agents, chat_room, max_turns=4)
            await scheduler.run()

        # turn 1,3 → agent1；turn 2,4 → agent2
        assert two_agents[0].generate_with_function_calling.call_count == 2
        assert two_agents[1].generate_with_function_calling.call_count == 2

    @pytest.mark.asyncio
    async def test_run_adds_response_to_chat_room(self, chat_room):
        agent = make_agent("alice", "world")
        with patch("service.scheduler_service.build_tools", return_value=[]):
            scheduler = Scheduler([agent], chat_room, max_turns=1)
            await scheduler.run()

        assert len(chat_room.messages) == 1
        assert chat_room.messages[0].content == "world"
        assert chat_room.messages[0].sender == "alice"

    @pytest.mark.asyncio
    async def test_run_skips_empty_response(self, chat_room):
        agent = make_agent("alice", "")
        with patch("service.scheduler_service.build_tools", return_value=[]):
            scheduler = Scheduler([agent], chat_room, max_turns=2)
            await scheduler.run()

        assert len(chat_room.messages) == 0

    @pytest.mark.asyncio
    async def test_run_stops_on_exception(self, chat_room):
        agent1 = make_agent("agent1", "ok")
        agent2 = make_agent("agent2")
        agent2.generate_with_function_calling = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("service.scheduler_service.build_tools", return_value=[]):
            scheduler = Scheduler([agent1, agent2], chat_room, max_turns=4)
            await scheduler.run()

        # 第 1 轮成功（agent1），第 2 轮异常（agent2）后退出
        assert len(chat_room.messages) == 1
        assert chat_room.messages[0].content == "ok"
