"""unit tests for service.scheduler_service"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import service.message_bus as message_bus
import service.room_service as room_service
import service.agent_service as agent_service
import service.scheduler_service as scheduler
from service.agent_service import Agent
from constants import MessageBusTopic


def _make_mock_agent(name: str) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.wait_event_queue = asyncio.Queue()
    return agent


@pytest.fixture(autouse=True)
def clean():
    message_bus.init()
    room_service.close_all()
    agent_service.close()
    scheduler.stop()
    yield
    scheduler.stop()
    agent_service.close()
    room_service.close_all()
    message_bus.stop()


def _setup_room_and_agents(room_name, agent_names, max_turns):
    """helper: 初始化 room、mock agents、scheduler"""
    room_service.init(room_name)
    room_service.setup_members(room_name, agent_names)

    mock_agents = {name: _make_mock_agent(name) for name in agent_names}

    agents_config = [{"name": n, "prompt_file": None, "model": "qwen"} for n in agent_names]
    rooms_config = [{"name": room_name, "agents": agent_names, "max_turns": max_turns}]

    with patch("service.agent_service.load_prompt", return_value="prompt"), \
         patch.object(agent_service, "_agents", mock_agents):
        scheduler.init(rooms_config)

    return mock_agents


class TestSchedulerRun:
    @pytest.mark.asyncio
    async def test_scheduler_exits_when_no_agents_activated(self):
        """没有任何事件发布时，scheduler.run() 应立即退出。"""
        scheduler.init([])
        await asyncio.wait_for(scheduler.run(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_scheduler_runs_agent_on_turn_event(self):
        """发布 ROOM_AGENT_TURN 后，scheduler 应调度对应 agent 处理事件。"""
        room_service.init("r1")
        room_service.setup_members("r1", ["alice"])

        alice = _make_mock_agent("alice")
        run_turn_calls = []

        async def fake_run_turn(a, room_name, max_function_calls):
            run_turn_calls.append((a.name, room_name))

        scheduler.init([{"name": "r1", "agents": ["alice"], "max_turns": 1}])

        with patch.object(agent_service, "_agents", {"alice": alice}), \
             patch("service.scheduler_service.agent_service.run_turn", fake_run_turn), \
             patch("service.scheduler_service.agent_service.get_agent", return_value=alice):
            # 手动触发事件（绕过 room.setup_turns）
            from service.message_bus import Message
            from model.agent_event import RoomMessageEvent
            alice.wait_event_queue.put_nowait(RoomMessageEvent("r1"))
            scheduler._active_agents.add("alice")
            scheduler._running["alice"] = asyncio.create_task(scheduler._run_agent(alice))

            await asyncio.wait_for(scheduler.run(), timeout=2.0)

        assert ("alice", "r1") in run_turn_calls

    @pytest.mark.asyncio
    async def test_run_agent_marks_inactive_after_queue_drained(self):
        """队列清空后，agent 应从 _active_agents 中移除。"""
        alice = _make_mock_agent("alice")
        scheduler._active_agents.add("alice")

        async def fake_handle(a, event):
            pass

        with patch("service.scheduler_service._handle_event", fake_handle), \
             patch("service.scheduler_service.agent_service.run_turn", AsyncMock()):
            from model.agent_event import RoomMessageEvent
            alice.wait_event_queue.put_nowait(RoomMessageEvent("r1"))
            await scheduler._run_agent(alice)

        assert "alice" not in scheduler._active_agents

    @pytest.mark.asyncio
    async def test_handle_event_error_does_not_propagate(self):
        """_handle_event 中 run_turn 抛出异常时不应向上传播。"""
        alice = _make_mock_agent("alice")
        from model.agent_event import RoomMessageEvent

        with patch("service.scheduler_service.agent_service.run_turn", AsyncMock(side_effect=RuntimeError("boom"))):
            # 不应 raise
            await scheduler._handle_event(alice, RoomMessageEvent("r1"))

    @pytest.mark.asyncio
    async def test_on_agent_turn_creates_task(self):
        """收到 ROOM_AGENT_TURN 消息后，_running 中应出现对应 task。"""
        alice = _make_mock_agent("alice")

        with patch("service.scheduler_service.agent_service.get_agent", return_value=alice), \
             patch("service.scheduler_service._run_agent", AsyncMock()):
            from service.message_bus import Message
            msg = Message(topic=MessageBusTopic.ROOM_AGENT_TURN, payload={"agent_name": "alice", "room_name": "r1"})
            scheduler._on_agent_turn(msg)

        assert "alice" in scheduler._active_agents
        assert not alice.wait_event_queue.empty()
