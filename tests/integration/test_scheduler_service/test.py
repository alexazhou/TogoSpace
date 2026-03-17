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
from ...base import ServiceTestCase

TEAM = "test_team"


def _make_mock_agent(name: str, team_name: str = TEAM) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.team_name = team_name
    agent.key = f"{name}@{team_name}"
    agent.wait_task_queue = asyncio.Queue()
    agent.consume_task = AsyncMock()
    return agent


class TestSchedulerRun(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await cls.areset_services()
        await room_service.startup()

    async def test_scheduler_run_terminates_on_stop(self):
        """调用 scheduler.shutdown() 后，scheduler.run() 应正常结束。"""
        await scheduler.startup([])
        run_task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.1)
        scheduler.shutdown()
        await asyncio.wait_for(run_task, timeout=2.0)

    async def test_scheduler_runs_agent_on_turn_event(self):
        """发布 ROOM_AGENT_TURN 后，scheduler 应触发 agent.consume_task。"""
        room_service.create_room(TEAM, "r1", ["alice"])
        alice = _make_mock_agent("alice")

        teams_config = [{"name": TEAM, "groups": [{"name": "r1", "members": ["alice"], "max_turns": 1}], "max_function_calls": 5}]
        await scheduler.startup(teams_config)

        with patch("service.scheduler_service.agent_service.get_agent", return_value=alice):
            run_task = asyncio.create_task(scheduler.run())

            msg = Message(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_name": "alice", "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            scheduler._on_agent_turn(msg)

            await asyncio.sleep(0.5)

            alice.consume_task.assert_called()

            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

    async def test_agent_is_active_self_contained(self):
        """验证 Agent 活跃状态的自治逻辑：基于 _is_running 或 队列深度。"""
        alice = Agent("alice", TEAM, "prompt", "model")

        assert alice.is_active is False

        alice.wait_task_queue.put_nowait(RoomMessageEvent(f"r1@{TEAM}"))
        assert alice.is_active is True

        alice.wait_task_queue.get_nowait()
        alice._is_running = True
        assert alice.is_active is True

        alice._is_running = False
        assert alice.is_active is False

    async def test_handle_event_error_logged_in_agent(self):
        """验证 Agent.consume_task 内部错误不导致崩溃。"""
        real_agent = Agent("test", TEAM, "prompt", "model")
        real_agent.wait_task_queue.put_nowait(RoomMessageEvent(f"r1@{TEAM}"))

        with patch.object(real_agent, "run_turn", side_effect=RuntimeError("boom")):
            await real_agent.consume_task(max_function_calls=5)

        assert real_agent.wait_task_queue.empty()

    async def test_on_agent_turn_creates_task(self):
        """收到 ROOM_AGENT_TURN 消息后，agent 任务入队并启动 Task。"""
        alice = _make_mock_agent("alice")
        teams_config = [{"name": TEAM, "groups": [{"name": "r1", "members": ["alice"], "max_turns": 1}], "max_function_calls": 5}]
        await scheduler.startup(teams_config)

        with patch("service.scheduler_service.agent_service.get_agent", return_value=alice):
            msg = Message(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_name": "alice", "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            scheduler._on_agent_turn(msg)

        assert not alice.wait_task_queue.empty()
        assert f"alice@{TEAM}" in scheduler._running
