"""integration tests for service.scheduler_service"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import service.room_service as room_service
import service.agent_service as agent_service
import service.scheduler_service as scheduler
from service.agent_service import Agent
from service.message_bus import Message
from model.agent_event import RoomMessageEvent
from constants import MessageBusTopic
from base import ServiceTestCase


def _make_mock_agent(name: str) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.wait_event_queue = asyncio.Queue()
    return agent


class TestSchedulerRun(ServiceTestCase):
    async def test_scheduler_exits_when_no_agents_activated(self):
        """没有任何事件发布时，scheduler.run() 应立即退出。"""
        scheduler.init([])
        await asyncio.wait_for(scheduler.run(), timeout=2.0)

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
            alice.wait_event_queue.put_nowait(RoomMessageEvent("r1"))
            scheduler._active_agents.add("alice")
            scheduler._running["alice"] = asyncio.create_task(scheduler._run_agent(alice))
            await asyncio.wait_for(scheduler.run(), timeout=2.0)

        assert ("alice", "r1") in run_turn_calls

    async def test_run_agent_marks_inactive_after_queue_drained(self):
        """队列清空后，agent 应从 _active_agents 中移除。"""
        alice = _make_mock_agent("alice")
        scheduler._active_agents.add("alice")

        async def fake_handle(a, event):
            pass

        with patch("service.scheduler_service._handle_event", fake_handle):
            alice.wait_event_queue.put_nowait(RoomMessageEvent("r1"))
            await scheduler._run_agent(alice)

        assert "alice" not in scheduler._active_agents

    async def test_handle_event_error_does_not_propagate(self):
        """_handle_event 中 run_turn 抛出异常时不应向上传播。"""
        alice = _make_mock_agent("alice")
        with patch("service.scheduler_service.agent_service.run_turn", AsyncMock(side_effect=RuntimeError("boom"))):
            await scheduler._handle_event(alice, RoomMessageEvent("r1"))

    async def test_on_agent_turn_creates_task(self):
        """收到 ROOM_AGENT_TURN 消息后，agent 应被标记为活跃，事件入队。"""
        alice = _make_mock_agent("alice")

        with patch("service.scheduler_service.agent_service.get_agent", return_value=alice), \
             patch("service.scheduler_service._run_agent", AsyncMock()):
            msg = Message(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_name": "alice", "room_name": "r1"},
            )
            scheduler._on_agent_turn(msg)

        assert "alice" in scheduler._active_agents
        assert not alice.wait_event_queue.empty()
