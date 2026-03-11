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
    agent.wait_task_queue = asyncio.Queue()
    agent.consume_task = AsyncMock()
    return agent


class TestSchedulerRun(ServiceTestCase):
    async def test_scheduler_run_terminates_on_stop(self):
        """调用 scheduler.stop() 后，scheduler.run() 应正常结束。"""
        scheduler.init([])
        run_task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.1)
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    async def test_scheduler_runs_agent_on_turn_event(self):
        """发布 ROOM_AGENT_TURN 后，scheduler 应触发 agent.consume_task。"""
        room_service.init("r1", ["alice"])
        alice = _make_mock_agent("alice")

        scheduler.init([{"name": "r1", "agents": ["alice"], "max_turns": 1}])

        with patch.object(agent_service, "_agents", {"alice": alice}), \
             patch("service.scheduler_service.agent_service.get_agent", return_value=alice):
            
            # 启动调度器
            run_task = asyncio.create_task(scheduler.run())
            
            # 手动触发一个 Turn 事件
            msg = Message(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_name": "alice", "room_name": "r1"},
            )
            scheduler._on_agent_turn(msg)
            
            # 稍等片刻让 task 启动
            await asyncio.sleep(0.5)
            
            # 验证 agent.consume_task 被调用
            alice.consume_task.assert_called()
            
            scheduler.stop()
            await asyncio.wait_for(run_task, timeout=2.0)

    async def test_is_agent_active_logic(self):
        """验证活跃状态判断逻辑：有任务运行或队列不为空。"""
        alice = _make_mock_agent("alice")
        
        with patch.object(agent_service, "_agents", {"alice": alice}), \
             patch("service.scheduler_service.agent_service.get_agent", return_value=alice):
            
            # 1. 初始状态：Idle
            assert scheduler.is_agent_active("alice") is False
            
            # 2. 队列不为空 -> Active
            alice.wait_task_queue.put_nowait(RoomMessageEvent("r1"))
            assert scheduler.is_agent_active("alice") is True
            
            # 3. 队列清空，但任务正在运行 -> Active
            alice.wait_task_queue.get_nowait()
            mock_task = MagicMock(spec=asyncio.Task)
            mock_task.done.return_value = False
            scheduler._running["alice"] = mock_task
            assert scheduler.is_agent_active("alice") is True
            
            # 4. 任务结束 -> Idle
            mock_task.done.return_value = True
            assert scheduler.is_agent_active("alice") is False

    async def test_handle_event_error_logged_in_agent(self):
        """验证 Agent.consume_task 内部错误不导致崩溃（通过检查代码逻辑确保）。"""
        real_agent = Agent("test", "prompt", "model")
        real_agent.wait_task_queue.put_nowait(RoomMessageEvent("r1"))
        
        with patch.object(real_agent, "run_turn", side_effect=RuntimeError("boom")):
            # 应该能正常结束而不抛出异常
            await real_agent.consume_task(max_function_calls=5)
        
        assert real_agent.wait_task_queue.empty()

    async def test_on_agent_turn_creates_task(self):
        """收到 ROOM_AGENT_TURN 消息后，agent 事件入队并启动 Task。"""
        alice = _make_mock_agent("alice")

        with patch("service.scheduler_service.agent_service.get_agent", return_value=alice):
            msg = Message(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_name": "alice", "room_name": "r1"},
            )
            scheduler._on_agent_turn(msg)

        assert not alice.wait_task_queue.empty()
        assert "alice" in scheduler._running
