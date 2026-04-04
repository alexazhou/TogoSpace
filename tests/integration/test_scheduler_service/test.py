"""integration tests for service.schedulerService"""
import asyncio
import logging
import os
from types import SimpleNamespace
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import service.roomService as roomService
import service.agentService as agentService
import service.schedulerService as scheduler
from service.agentService import Agent
from service.messageBus import EventBusMessage
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from constants import MessageBusTopic, AgentStatus, AgentTaskType, AgentTaskStatus
from util.configTypes import TeamConfig
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def _make_mock_agent(name: str, team_name: str = TEAM, agent_id: int = 1) -> Agent:
    """构造最小可运行的 Agent mock，用于观察 scheduler 调度行为。"""
    agent = MagicMock(spec=Agent)
    agent.gt_agent = SimpleNamespace(id=agent_id, team_id=1, name=name, model="mock")
    agent.status = AgentStatus.IDLE
    agent.max_function_calls = 5
    agent.current_db_task = None
    agent.consume_task = AsyncMock()
    agent.has_pending_tasks = AsyncMock(return_value=False)
    return agent


def _make_team_config() -> TeamConfig:
    return TeamConfig.model_validate({
        "name": TEAM,
        "agents": [{"name": "alice", "role_template": "alice"}],
        "preset_rooms": [{"name": "r1", "agents": ["alice"], "max_turns": 1}],
    })


def _patch_scheduler_teams(monkeypatch, teams: list[SimpleNamespace] | None = None) -> None:
    return None


def _patch_scheduler_rooms(monkeypatch, *rooms: roomService.ChatRoom) -> None:
    room_map = {room.room_id: room for room in rooms}
    monkeypatch.setattr(scheduler.chat_room, "get_room", lambda room_id: room_map.get(room_id))



class TestSchedulerRun(ServiceTestCase):
    def setup_method(self):
        # 清理可能残留的 scheduler 状态，避免测试间污染
        scheduler.shutdown()

    async def test_scheduler_run_terminates_on_stop(self, monkeypatch):
        """调用 scheduler.shutdown() 后，scheduler.run() 应正常结束。"""
        await roomService.startup()
        _patch_scheduler_teams(monkeypatch)
        await scheduler.startup()
        run_task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.1)
        scheduler.shutdown()
        await asyncio.wait_for(run_task, timeout=2.0)

    async def test_scheduler_runs_agent_on_turn_event(self, monkeypatch):
        """发布 ROOM_AGENT_TURN 后，scheduler 应触发 Agent 的消费 task 管理。"""
        alice = _make_mock_agent("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="alice", role_template_id=1)],
        )

        _patch_scheduler_teams(monkeypatch, [SimpleNamespace(name=TEAM, max_function_calls=5)])
        _patch_scheduler_rooms(monkeypatch, room)
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": room.room_id},
            ))
            mock_task_manager.get_pending_tasks = AsyncMock(return_value=[])
            run_task = asyncio.create_task(scheduler.run())

            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_id": 1, "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            await scheduler._on_agent_turn(msg)

            # scheduler 内部只做委派，给一个短暂让渡时间以保持测试时序稳定。
            await asyncio.sleep(0.5)

            alice.ensure_consumer_task_running.assert_called_once_with()

            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

    async def test_agent_is_active_based_on_status_and_current_db_task(self):
        """验证 Agent 活跃状态：基于 status 或 current_db_task。"""
        alice = Agent(GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="model"), "prompt")

        assert alice.is_active is False

        alice.status = AgentStatus.ACTIVE
        assert alice.is_active is True

        alice.status = AgentStatus.IDLE
        assert alice.is_active is False

        # 有 current_db_task 时也是活跃的
        alice.current_db_task = GtAgentTask(id=1, agent_id=1, task_type=AgentTaskType.ROOM_MESSAGE, task_data={"room_id": 1})
        assert alice.is_active is True

    async def test_handle_event_error_logged_in_agent(self):
        """验证 Agent.consume_task 内部错误后进入 FAILED 状态。"""
        real_agent = Agent(GtAgent(id=1, team_id=1, name="test", role_template_id=1, model="model"), "prompt")

        with patch("service.agentService.agent.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.get_first_pending_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": 1},
            ))
            mock_task_manager.claim_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": 1},
                status=AgentTaskStatus.RUNNING,
            ))
            mock_task_manager.update_task_status = AsyncMock()

            with patch.object(real_agent, "run_chat_turn", side_effect=RuntimeError("boom")):
                await real_agent.consume_task(max_function_calls=5)

        assert real_agent.status == AgentStatus.FAILED

    async def test_on_agent_turn_creates_task(self, monkeypatch):
        """收到 ROOM_AGENT_TURN 消息后，创建任务并触发消费 task 启动。"""
        alice = _make_mock_agent("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="alice", role_template_id=1)],
        )
        _patch_scheduler_teams(monkeypatch, [SimpleNamespace(name=TEAM, max_function_calls=5)])
        _patch_scheduler_rooms(monkeypatch, room)
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": room.room_id},
            ))
            mock_task_manager.get_pending_tasks = AsyncMock(return_value=[])
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_id": 1, "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            await scheduler._on_agent_turn(msg)

        alice.ensure_consumer_task_running.assert_called_once_with()

    async def test_duplicate_room_event_is_skipped(self, monkeypatch):
        """同一房间连续触发两次 ROOM_AGENT_TURN，第二次应被跳过。"""
        alice = _make_mock_agent("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="alice", role_template_id=1)],
        )
        _patch_scheduler_teams(monkeypatch, [SimpleNamespace(name=TEAM, max_function_calls=5)])
        _patch_scheduler_rooms(monkeypatch, room)
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": room.room_id},
            ))
            # 第一次调用返回已有任务，第二次调用返回空列表
            mock_task_manager.get_pending_tasks = AsyncMock(return_value=[
                GtAgentTask(id=1, agent_id=1, task_type=AgentTaskType.ROOM_MESSAGE, task_data={"room_id": room.room_id}),
            ])
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_id": 1, "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            await scheduler._on_agent_turn(msg)

            # 第二次调用：get_pending_tasks 返回已有任务，create_task 不应被调用
            create_call_count = mock_task_manager.create_task.call_count
            await scheduler._on_agent_turn(msg)

            assert mock_task_manager.create_task.call_count == create_call_count

    async def test_different_rooms_not_deduplicated(self, monkeypatch):
        """不同房间的事件不应被去重，各自独立创建任务。"""
        alice = _make_mock_agent("alice")
        r1 = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="alice", role_template_id=1)],
        )
        r2 = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=2,
                team_id=1,
                name="r2",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="alice", role_template_id=1)],
        )
        _patch_scheduler_teams(monkeypatch, [SimpleNamespace(name=TEAM, max_function_calls=5)])
        _patch_scheduler_rooms(monkeypatch, r1, r2)
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(side_effect=[
                GtAgentTask(id=1, agent_id=1, task_type=AgentTaskType.ROOM_MESSAGE, task_data={"room_id": r1.room_id}),
                GtAgentTask(id=2, agent_id=1, task_type=AgentTaskType.ROOM_MESSAGE, task_data={"room_id": r2.room_id}),
            ])
            mock_task_manager.get_pending_tasks = AsyncMock(return_value=[])
            msg_r1 = EventBusMessage(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_id": 1, "room_id": r1.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            msg_r2 = EventBusMessage(
                topic=MessageBusTopic.ROOM_AGENT_TURN,
                payload={"agent_id": 1, "room_id": r2.room_id, "room_name": "r2", "room_key": f"r2@{TEAM}", "team_name": TEAM},
            )
            await scheduler._on_agent_turn(msg_r1)
            await scheduler._on_agent_turn(msg_r2)

        assert mock_task_manager.create_task.call_count == 2

    async def test_stop_team(self, monkeypatch):
        """验证停止特定团队的调度。"""
        alice = _make_mock_agent("alice")
        _patch_scheduler_teams(monkeypatch, [SimpleNamespace(name=TEAM, max_function_calls=5)])
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_team_agents", return_value=[alice]):
            scheduler.stop_team(1)

        alice.stop_consumer_task.assert_called_once_with()

    async def test_on_agent_turn_agent_not_found(self, monkeypatch):
        """验证 Agent 找不到时会直接抛出异常。"""
        _patch_scheduler_teams(monkeypatch)
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="non-existent", role_template_id=1)],
        )
        _patch_scheduler_rooms(monkeypatch, room)
        await scheduler.startup()
        msg = EventBusMessage(
            topic=MessageBusTopic.ROOM_AGENT_TURN,
            payload={"agent_id": 1, "room_id": 1, "team_name": TEAM},
        )
        with patch("service.schedulerService.agentService.get_agent", side_effect=KeyError("not found")):
            with pytest.raises(KeyError, match="not found"):
                await scheduler._on_agent_turn(msg)

    async def test_on_agent_turn_general_exception(self, monkeypatch):
        """验证获取 Agent 发生通用异常时会直接抛出。"""
        _patch_scheduler_teams(monkeypatch)
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                agent_read_index=None,
                updated_at=GtRoom._now(),
            ),
            agents=[GtAgent(id=1, team_id=1, name="error-agent", role_template_id=1)],
        )
        _patch_scheduler_rooms(monkeypatch, room)
        await scheduler.startup()
        msg = EventBusMessage(
            topic=MessageBusTopic.ROOM_AGENT_TURN,
            payload={"agent_id": 1, "room_id": 1, "team_name": TEAM},
        )
        with patch("service.schedulerService.agentService.get_agent", side_effect=RuntimeError("unexpected")):
            with pytest.raises(RuntimeError, match="unexpected"):
                await scheduler._on_agent_turn(msg)

    async def test_stop_agent_task_non_existent(self):
        """停止不存在的 agent task 不应报错。"""
        scheduler.stop_agent_task(-1)
        # No exception means success

    async def test_stop_agent_task_delegates_to_agent(self):
        """stop_agent_task 应委派给 Agent 自身的消费 task 管理。"""
        alice = _make_mock_agent("alice")
        with patch("service.schedulerService.agentService.get_agent", return_value=alice):
            scheduler.stop_agent_task(alice.gt_agent.id)
        alice.stop_consumer_task.assert_called_once_with()
