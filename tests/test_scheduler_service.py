import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from service.agent_service import Agent
from service.scheduler_service import Scheduler
import service.agent_service as agent_service
import service.chat_room_service as chat_room


def make_agent(name="agent", reply="hello"):
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.generate_with_function_calling = AsyncMock(return_value=(reply, []))
    return agent


ROOM = "test_room"


@pytest.fixture(autouse=True)
def setup_services():
    chat_room.init(ROOM)
    yield
    agent_service.close()
    chat_room.close_all()


class TestScheduler:
    @pytest.mark.asyncio
    async def test_run_calls_each_agent_in_order(self):
        agents = [make_agent("agent1", "reply1"), make_agent("agent2", "reply2")]
        with patch("service.agent_service._agents", agents), \
             patch("service.agent_tool_service.get_tools", return_value=[]):
            scheduler = Scheduler(room_name=ROOM, max_turns=4)
            await scheduler.run()

        # turn 1,3 → agent1；turn 2,4 → agent2
        assert agents[0].generate_with_function_calling.call_count == 2
        assert agents[1].generate_with_function_calling.call_count == 2

    @pytest.mark.asyncio
    async def test_run_adds_response_to_chat_room(self):
        agents = [make_agent("alice", "world")]
        with patch("service.agent_service._agents", agents), \
             patch("service.agent_tool_service.get_tools", return_value=[]):
            scheduler = Scheduler(room_name=ROOM, max_turns=1)
            await scheduler.run()

        room = chat_room.get_room(ROOM)
        assert len(room.messages) == 1
        assert room.messages[0].content == "world"
        assert room.messages[0].sender == "alice"

    @pytest.mark.asyncio
    async def test_run_skips_empty_response(self):
        agents = [make_agent("alice", "")]
        with patch("service.agent_service._agents", agents), \
             patch("service.agent_tool_service.get_tools", return_value=[]):
            scheduler = Scheduler(room_name=ROOM, max_turns=2)
            await scheduler.run()

        assert len(chat_room.get_room(ROOM).messages) == 0

    @pytest.mark.asyncio
    async def test_run_stops_on_exception(self):
        agent1 = make_agent("agent1", "ok")
        agent2 = make_agent("agent2")
        agent2.generate_with_function_calling = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("service.agent_service._agents", [agent1, agent2]), \
             patch("service.agent_tool_service.get_tools", return_value=[]):
            scheduler = Scheduler(room_name=ROOM, max_turns=4)
            await scheduler.run()

        # 第 1 轮成功（agent1），第 2 轮异常（agent2）后退出
        room = chat_room.get_room(ROOM)
        assert len(room.messages) == 1
        assert room.messages[0].content == "ok"
