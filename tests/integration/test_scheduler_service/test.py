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
from constants import MessageBusTopic, AgentStatus, AgentTaskType, AgentTaskStatus, ScheduleState
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
    agent.current_db_task = None
    agent.consume_task = AsyncMock()
    return agent


def _force_schedule_running() -> None:
    """强制将调度状态设为 RUNNING，供需要闸门打开的测试使用。"""
    scheduler._schedule_state = ScheduleState.RUNNING


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


def _make_scheduling_payload(room: roomService.ChatRoom, gt_agent) -> dict:
    """构建 need_scheduling=True 的标准事件 payload。"""
    return {
        "gt_room": room.gt_room,
        "need_scheduling": True,
        "current_turn_agent": gt_agent,
    }


class TestSchedulerRun(ServiceTestCase):
    def setup_method(self):
        # 清理可能残留的 scheduler 状态，避免测试间污染
        scheduler.shutdown()

    async def test_scheduler_shutdown_stops_all_agent_consumer_tasks(self, monkeypatch):
        """调用 scheduler.shutdown() 后，应停止所有 Agent 的消费协程。"""
        alice = _make_mock_agent("alice")
        bob = _make_mock_agent("bob", agent_id=2)
        await roomService.startup()
        _patch_scheduler_teams(monkeypatch)
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_all_agents", return_value=[alice, bob]):
            scheduler.shutdown()

        alice.stop_consumer_task.assert_called_once_with()
        bob.stop_consumer_task.assert_called_once_with()

    async def test_scheduler_runs_agent_on_turn_event(self, monkeypatch):
        """收到 need_scheduling=True 的事件后，scheduler 应触发 Agent 启动消费协程。"""
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
        _force_schedule_running()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": room.room_id},
            ))
            mock_task_manager.has_pending_room_task = AsyncMock(return_value=False)
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload=_make_scheduling_payload(room, alice.gt_agent),
            )
            await scheduler._on_room_status_changed(msg)

            # scheduler 内部只做委派，给一个短暂让渡时间以保持测试时序稳定。
            await asyncio.sleep(0.5)

            alice.start_consumer_task.assert_called_once_with()

    async def test_agent_is_active_based_on_status_and_current_db_task(self):
        """验证 Agent 活跃状态：基于 status 或 current_db_task。"""
        alice = Agent(GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="model"), "prompt")

        assert alice.is_active is False

        alice.task_consumer.status = AgentStatus.ACTIVE
        assert alice.is_active is True

        alice.task_consumer.status = AgentStatus.IDLE
        assert alice.is_active is False

    async def test_handle_event_error_logged_in_agent(self):
        """验证 Agent.task_consumer.consume 内部错误后进入 FAILED 状态。"""
        real_agent = Agent(GtAgent(id=1, team_id=1, name="test", role_template_id=1, model="model"), "prompt")

        with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_task_manager, \
             patch("service.agentService.agentTaskConsumer.agentActivityService") as mock_activity_svc:
            mock_activity_svc.add_activity = AsyncMock()
            mock_task_manager.get_first_unfinish_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": 1},
            ))
            mock_task_manager.transition_task_status = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": 1},
                status=AgentTaskStatus.RUNNING,
            ))
            mock_task_manager.update_task_status = AsyncMock()

            with patch.object(real_agent.task_consumer._turn_runner, "run_chat_turn", side_effect=RuntimeError("boom")):
                await real_agent.task_consumer.consume()

        assert real_agent.status == AgentStatus.FAILED

    async def test_failed_agent_does_not_restart_consumer_when_pending_tasks_remain(self):
        """任务失败后，即使仍有 pending task，也不应自动续起消费。"""
        real_agent = Agent(GtAgent(id=1, team_id=1, name="test", role_template_id=1, model="model"), "prompt")

        with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_task_manager, \
             patch("service.agentService.agentTaskConsumer.agentActivityService") as mock_activity_svc:
            mock_activity_svc.add_activity = AsyncMock()
            mock_task_manager.get_first_unfinish_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": 1},
            ))
            mock_task_manager.transition_task_status = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": 1},
                status=AgentTaskStatus.RUNNING,
            ))
            mock_task_manager.update_task_status = AsyncMock()
            mock_task_manager.has_consumable_task = AsyncMock(return_value=True)

            with patch.object(real_agent.task_consumer._turn_runner, "run_chat_turn", side_effect=RuntimeError("boom")):
                restart_spy = MagicMock()
                real_agent.task_consumer.start = restart_spy
                await real_agent.task_consumer.consume()

        assert real_agent.status == AgentStatus.FAILED
        restart_spy.assert_not_called()

    async def test_on_agent_turn_creates_task(self, monkeypatch):
        """收到 need_scheduling=True 消息后，创建任务并触发消费协程启动。"""
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
        _force_schedule_running()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": room.room_id},
            ))
            mock_task_manager.has_pending_room_task = AsyncMock(return_value=False)
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload=_make_scheduling_payload(room, alice.gt_agent),
            )
            await scheduler._on_room_status_changed(msg)

        alice.start_consumer_task.assert_called_once_with()

    async def test_need_scheduling_false_skips_scheduling(self, monkeypatch):
        """need_scheduling=False 时不应创建任务（特殊成员、IDLE 等场景均由 roomService 设置该标志）。"""
        await scheduler.startup()

        with patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload={"need_scheduling": False},
            )
            await scheduler._on_room_status_changed(msg)

        mock_task_manager.create_task.assert_not_called()

    async def test_duplicate_room_event_is_skipped(self, monkeypatch):
        """同一房间连续触发两次调度事件，第二次应被跳过。"""
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
        _force_schedule_running()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(return_value=GtAgentTask(
                id=1,
                agent_id=1,
                task_type=AgentTaskType.ROOM_MESSAGE,
                task_data={"room_id": room.room_id},
            ))
            mock_task_manager.has_pending_room_task = AsyncMock(return_value=True)
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload=_make_scheduling_payload(room, alice.gt_agent),
            )
            await scheduler._on_room_status_changed(msg)

            # 已存在同房间 pending 任务时，create_task 不应被调用
            create_call_count = mock_task_manager.create_task.call_count
            await scheduler._on_room_status_changed(msg)

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
        _force_schedule_running()

        with patch("service.schedulerService.agentService.get_agent", return_value=alice), \
             patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            mock_task_manager.create_task = AsyncMock(side_effect=[
                GtAgentTask(id=1, agent_id=1, task_type=AgentTaskType.ROOM_MESSAGE, task_data={"room_id": r1.room_id}),
                GtAgentTask(id=2, agent_id=1, task_type=AgentTaskType.ROOM_MESSAGE, task_data={"room_id": r2.room_id}),
            ])
            mock_task_manager.has_pending_room_task = AsyncMock(side_effect=[False, False])
            msg_r1 = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload=_make_scheduling_payload(r1, alice.gt_agent),
            )
            msg_r2 = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload=_make_scheduling_payload(r2, alice.gt_agent),
            )
            await scheduler._on_room_status_changed(msg_r1)
            await scheduler._on_room_status_changed(msg_r2)

        assert mock_task_manager.create_task.call_count == 2

    async def test_stop_scheduler_team(self, monkeypatch):
        """验证停止特定团队的调度。"""
        alice = _make_mock_agent("alice")
        _patch_scheduler_teams(monkeypatch, [SimpleNamespace(name=TEAM, max_function_calls=5)])
        await scheduler.startup()

        with patch("service.schedulerService.agentService.get_team_agents", return_value=[alice]):
            scheduler.stop_scheduler_team(1)

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
        _force_schedule_running()
        msg = EventBusMessage(
            topic=MessageBusTopic.ROOM_STATUS_CHANGED,
            payload=_make_scheduling_payload(room, GtAgent(id=1, team_id=1, name="non-existent", role_template_id=1)),
        )
        with patch("service.schedulerService.agentService.get_agent", side_effect=KeyError("not found")):
            with pytest.raises(KeyError, match="not found"):
                await scheduler._on_room_status_changed(msg)

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
        _force_schedule_running()
        msg = EventBusMessage(
            topic=MessageBusTopic.ROOM_STATUS_CHANGED,
            payload=_make_scheduling_payload(room, GtAgent(id=1, team_id=1, name="error-agent", role_template_id=1)),
        )
        with patch("service.schedulerService.agentService.get_agent", side_effect=RuntimeError("unexpected")):
            with pytest.raises(RuntimeError, match="unexpected"):
                await scheduler._on_room_status_changed(msg)

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


class TestScheduleGate(ServiceTestCase):
    """调度闸门（ScheduleState）相关测试。"""

    def setup_method(self):
        scheduler.shutdown()

    async def test_initial_state_is_stopped(self):
        """scheduler 初始化后状态应为 STOPPED。"""
        assert scheduler.get_schedule_state() == ScheduleState.STOPPED
        assert scheduler.get_schedule_state() != ScheduleState.RUNNING

    async def test_start_schedule_sets_running_when_initialized(self):
        """LLM 已配置时，start_schedule 应切到 RUNNING。"""
        await scheduler.startup()
        with patch("service.schedulerService.configUtil.is_initialized", return_value=True), \
             patch("service.schedulerService.chat_room.activate_rooms", new_callable=AsyncMock):
            await scheduler.start_schedule()

        assert scheduler.get_schedule_state() == ScheduleState.RUNNING
        assert scheduler.get_schedule_state() == ScheduleState.RUNNING

    async def test_start_schedule_sets_blocked_when_not_initialized(self):
        """LLM 未配置时，start_schedule 应切到 BLOCKED。"""
        await scheduler.startup()
        with patch("service.schedulerService.configUtil.is_initialized", return_value=False):
            await scheduler.start_schedule()

        assert scheduler.get_schedule_state() == ScheduleState.BLOCKED
        assert scheduler.get_schedule_state() != ScheduleState.RUNNING

    async def test_start_schedule_activates_rooms(self):
        """start_schedule 进入 RUNNING 后应调用 activate_rooms(None)。"""
        await scheduler.startup()
        with patch("service.schedulerService.configUtil.is_initialized", return_value=True), \
             patch("service.schedulerService.chat_room.activate_rooms", new_callable=AsyncMock) as mock_activate:
            await scheduler.start_schedule()

        mock_activate.assert_awaited_once_with(None)

    async def test_stop_schedule_resets_to_stopped(self):
        """stop_schedule 应将状态重置为 STOPPED。"""
        _force_schedule_running()
        assert scheduler.get_schedule_state() == ScheduleState.RUNNING

        scheduler.stop_schedule()

        assert scheduler.get_schedule_state() == ScheduleState.STOPPED
        assert scheduler.get_schedule_state() != ScheduleState.RUNNING

    async def test_event_blocked_when_not_running(self, monkeypatch):
        """调度未 RUNNING 时，need_scheduling=True 的事件不应创建任务。"""
        await scheduler.startup()
        # startup 后状态为 STOPPED
        assert scheduler.get_schedule_state() == ScheduleState.STOPPED

        with patch("service.schedulerService.gtAgentTaskManager") as mock_task_manager:
            msg = EventBusMessage(
                topic=MessageBusTopic.ROOM_STATUS_CHANGED,
                payload={
                    "need_scheduling": True,
                    "gt_room": SimpleNamespace(id=1),
                    "current_turn_agent": GtAgent(id=1, team_id=1, name="alice", role_template_id=1),
                },
            )
            await scheduler._on_room_status_changed(msg)

        mock_task_manager.create_task.assert_not_called()

    async def test_start_scheduling_skips_when_not_running(self):
        """start_scheduling 在非 RUNNING 状态下应跳过。"""
        await scheduler.startup()
        with patch("service.schedulerService.chat_room.activate_rooms", new_callable=AsyncMock) as mock_activate:
            await scheduler.start_scheduling(team_name="some_team")

        mock_activate.assert_not_called()

    async def test_shutdown_resets_state(self):
        """shutdown 应将状态重置为 STOPPED。"""
        _force_schedule_running()
        scheduler.shutdown()
        assert scheduler.get_schedule_state() == ScheduleState.STOPPED
