"""integration tests for task-driven agent wakeup (collaboration task flow).

验证范围：
  1. 任务优先级：ROOM_MESSAGE 调度优先于 TODO_TASK
  2. 自动调度：ROOM_MESSAGE turn 完成后，若有 TODO/IN_PROGRESS 协作任务，自动创建调度记录
  3. 幂等保护：重复触发不会创建多条 TODO_TASK 调度记录
  4. Turn 执行：TODO_TASK turn 注入任务上下文后正常完成
  5. 自动重唤醒：agent 调用 finish_action 但未完成协作任务时，下一轮 turn 结束后再次创建调度
"""
import asyncio
import os
import sys

import pytest

from constants import AgentTaskStatus, AgentTaskType, AgentStatus, TaskStatus
from dal.db import gtAgentManager, gtTeamManager, gtScheculeTaskManager, gtAgentTaskManager
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtScheculeTask import GtScheculeTask
from service import presetService, agentService, roomService, ormService, persistenceService, funcToolService
from util import configUtil
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _CollabTaskCase(ServiceTestCase):
    """协作任务集成测试基类：统一加载测试专用 agent/team 配置。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        await funcToolService.startup()
        cfg = configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)
        team_cfg = cfg.teams[0]
        await presetService._import_team_from_config(team_cfg)
        await agentService.startup()
        await agentService.load_all_team_agents()
        await roomService.load_all_rooms()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _get_alice(self):
        team = await gtTeamManager.get_team(TEAM)
        alice_db = await gtAgentManager.get_agent(team.id, "alice")
        return team, alice_db, agentService.get_agent(alice_db.id)

    async def _create_collab_task(self, agent_id: int, team_id: int, title: str = "测试协作任务", description: str = "", initial_status: TaskStatus = TaskStatus.IN_PROGRESS) -> GtAgentTask:
        """创建一条协作任务，默认状态为 IN_PROGRESS（模拟已开始处理的任务）。"""
        task = GtAgentTask(
            team_id=team_id,
            title=title,
            description=description,
            assignee_id=agent_id,
            creator_id=agent_id,  # 补全非空字段
            status=initial_status,
        )
        return await gtAgentTaskManager.create_task(task)


class TestTaskPriority(_CollabTaskCase):
    """验证 ROOM_MESSAGE 调度优先于 TODO_TASK 调度。"""

    async def test_room_message_priority_over_collaboration_task(self):
        """同时存在 ROOM_MESSAGE 和 TODO_TASK 调度时，get_first_unfinish_task 应先返回 ROOM_MESSAGE。"""
        team, alice_db, _ = await self._get_alice()

        collab_sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": 9999},
        )
        room_sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.ROOM_MESSAGE,
            {"room_id": 9999},
        )
        try:
            first = await gtScheculeTaskManager.get_first_unfinish_task(alice_db.id)
            assert first is not None
            assert first.task_type == AgentTaskType.ROOM_MESSAGE, (
                f"期望 ROOM_MESSAGE 优先，但得到 {first.task_type}"
            )
        finally:
            await gtScheculeTaskManager.update_task_status(collab_sched.id, AgentTaskStatus.CANCELLED)
            await gtScheculeTaskManager.update_task_status(room_sched.id, AgentTaskStatus.CANCELLED)

    async def test_collaboration_task_scheduled_after_room_message_gone(self):
        """当 ROOM_MESSAGE 调度已完成时，get_first_unfinish_task 应返回 TODO_TASK。"""
        team, alice_db, _ = await self._get_alice()

        collab_sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": 9998},
        )
        try:
            first = await gtScheculeTaskManager.get_first_unfinish_task(alice_db.id)
            assert first is not None
            assert first.task_type == AgentTaskType.TODO_TASK
        finally:
            await gtScheculeTaskManager.update_task_status(collab_sched.id, AgentTaskStatus.CANCELLED)


class TestAutoSchedule(_CollabTaskCase):
    """验证 turn 完成后自动调度协作任务。"""

    async def test_collaboration_task_auto_scheduled_after_turn_completion(self):
        """consume 完成 ROOM_MESSAGE turn 后，若有 IN_PROGRESS 协作任务，应自动创建 TODO_TASK 调度。"""
        await self.create_room(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        team, alice_db, alice = await self._get_alice()

        agent_task = await self._create_collab_task(alice_db.id, team.id, title="待处理任务")

        room_sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.ROOM_MESSAGE,
            {"room_id": room.room_id},
        )
        await room.activate_scheduling()
        bob_id = agentService.get_agent_id_by_stable_name(room.team_id, "bob")
        await room.add_message(bob_id, "hello")

        # 响应顺序：1) ROOM_MESSAGE turn 结束（无发言）; 2-3) 自动触发的 TODO_TASK turn 完成任务
        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "finish_action", "arguments": {"confirm_no_need_talk": True}}]},
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "已完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()

        # ROOM_MESSAGE 调度应已完成
        completed_sched = await gtScheculeTaskManager.get_task(room_sched.id)
        assert completed_sched.status == AgentTaskStatus.COMPLETED

        # 应已自动创建并完成了 TODO_TASK 调度（验证 auto-schedule 机制）
        all_scheds = list(await GtScheculeTask.select().where(
            GtScheculeTask.agent_id == alice_db.id,
            GtScheculeTask.task_type == AgentTaskType.TODO_TASK,
        ).aio_execute())
        assert len(all_scheds) >= 1, "ROOM_MESSAGE turn 完成后，应自动创建协作任务调度"

        # 清理
        agent_task.status = TaskStatus.DONE
        await gtAgentTaskManager.update_task(agent_task, [GtAgentTask.status])

    async def test_collaboration_task_not_duplicated_idempotent(self):
        """若已存在 PENDING 的 TODO_TASK 调度，重复触发不应创建第二条。"""
        team, alice_db, _ = await self._get_alice()

        agent_task = await self._create_collab_task(alice_db.id, team.id, title="幂等测试任务")

        # 先手动创建一条
        sched1 = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )

        # 再调用自动调度方法
        consumer = agentService.get_agent(alice_db.id).task_consumer
        await consumer._check_and_schedule_collaboration_tasks()

        # 查询 PENDING 中的 TODO_TASK 调度，应只有 1 条
        all_tasks = list(
            await GtScheculeTask.select()
            .where(
                GtScheculeTask.agent_id == alice_db.id,
                GtScheculeTask.task_type == AgentTaskType.TODO_TASK,
                GtScheculeTask.status == AgentTaskStatus.PENDING,
            )
            .aio_execute()
        )
        matching = [t for t in all_tasks if t.task_data.get("agent_task_id") == agent_task.id]
        assert len(matching) == 1, f"期望 1 条 PENDING TODO_TASK，实际 {len(matching)} 条"

        # 清理
        await gtScheculeTaskManager.update_task_status(sched1.id, AgentTaskStatus.CANCELLED)
        agent_task.status = TaskStatus.DONE
        await gtAgentTaskManager.update_task(agent_task, [GtAgentTask.status])

    async def test_no_schedule_when_no_active_collab_task(self):
        """无 TODO/IN_PROGRESS 协作任务时，_check_and_schedule_collaboration_tasks 不应创建调度。"""
        team, alice_db, alice = await self._get_alice()

        before_count = len(list(
            await GtScheculeTask.select()
            .where(
                GtScheculeTask.agent_id == alice_db.id,
                GtScheculeTask.task_type == AgentTaskType.TODO_TASK,
                GtScheculeTask.status == AgentTaskStatus.PENDING,
            )
            .aio_execute()
        ))

        await alice.task_consumer._check_and_schedule_collaboration_tasks()

        after_count = len(list(
            await GtScheculeTask.select()
            .where(
                GtScheculeTask.agent_id == alice_db.id,
                GtScheculeTask.task_type == AgentTaskType.TODO_TASK,
                GtScheculeTask.status == AgentTaskStatus.PENDING,
            )
            .aio_execute()
        ))
        assert after_count == before_count, "无活跃协作任务时不应创建调度"


class TestCollaborationTaskTurnExecution(_CollabTaskCase):
    """验证 TODO_TASK turn 的执行逻辑。"""

    async def test_collaboration_task_turn_injects_task_context_into_history(self):
        """执行 TODO_TASK turn 后，历史中应包含任务通知提示。"""
        team, alice_db, alice = await self._get_alice()

        agent_task = await self._create_collab_task(
            alice_db.id, team.id,
            title="编写单元测试",
            description="为 foo 模块添加测试",
        )
        sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )

        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "已完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()
        from constants import AgentHistoryTag
        history = alice.task_consumer._turn_runner._history
        turn_begin_entries = [h for h in history if AgentHistoryTag.ROOM_TURN_BEGIN in (h.tags or [])]
        assert len(turn_begin_entries) >= 1
        content = turn_begin_entries[-1].content or ""
        assert "任务通知" in content
        assert "编写单元测试" in content

        # 调度记录应已 COMPLETED
        completed_sched = await gtScheculeTaskManager.get_task(sched.id)
        assert completed_sched.status == AgentTaskStatus.COMPLETED

        # 清理
        agent_task.status = TaskStatus.DONE
        await gtAgentTaskManager.update_task(agent_task, [GtAgentTask.status])

    async def test_collaboration_task_turn_completes_without_room(self):
        """TODO_TASK turn 不依赖房间，finish_action 应成功终止 turn。"""
        team, alice_db, alice = await self._get_alice()

        agent_task = await self._create_collab_task(alice_db.id, team.id, title="无房间任务")
        sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )

        completed = False

        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "已完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()
            completed = True

        assert completed, "TODO_TASK turn 应正常完成（不因缺少房间而异常）"

        completed_sched = await gtScheculeTaskManager.get_task(sched.id)
        assert completed_sched.status == AgentTaskStatus.COMPLETED

        # 清理
        agent_task.status = TaskStatus.DONE
        await gtAgentTaskManager.update_task(agent_task, [GtAgentTask.status])

    async def test_collaboration_task_rewake_if_not_completed(self):
        """Agent 调用 finish_action 完成 turn，但协作任务仍为 IN_PROGRESS（未标记完成），应自动再次创建调度（重唤醒）。"""
        team, alice_db, alice = await self._get_alice()

        # 创建一个保持 IN_PROGRESS 状态的协作任务（agent 不调用 update_task 更新为 DONE）
        agent_task = await self._create_collab_task(alice_db.id, team.id, title="未完成协作任务")
        sched1 = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )

        # Agent 第一轮只调用 finish_action，不更新任务状态 → 触发重唤醒
        # 第二轮（自动创建的重唤醒调度）：更新任务为 DONE 后再 finish_action → 循环正常结束
        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()

        # 第一轮调度应 COMPLETED
        completed_sched = await gtScheculeTaskManager.get_task(sched1.id)
        assert completed_sched.status == AgentTaskStatus.COMPLETED

        # 协作任务未完成时，应自动创建了第二条唤醒调度（重唤醒机制）
        all_todo_scheds = list(await GtScheculeTask.select().where(
            GtScheculeTask.agent_id == alice_db.id,
            GtScheculeTask.task_type == AgentTaskType.TODO_TASK,
        ).aio_execute())
        rewake_scheds = [s for s in all_todo_scheds if s.id != sched1.id]
        assert len(rewake_scheds) >= 1, "协作任务未完成时，应自动创建新的唤醒调度（重唤醒）"

        # 清理（任务已由第二轮置为 DONE，无需额外清理）


class TestTaskDrivenToolUsage(_CollabTaskCase):
    """验证在任务驱动 Turn 中调用工具的正确性。"""

    async def test_send_chat_msg_success_in_task_turn(self):
        """在任务驱动 Turn 中（无 room 上下文），应允许向所属房间发消息汇报进度。"""
        await self.create_room(TEAM, "collab_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"collab_room@{TEAM}")
        team, alice_db, alice = await self._get_alice()

        agent_task = await self._create_collab_task(alice_db.id, team.id, title="汇报任务")
        await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )

        # 模拟模型：先发消息，再改状态，最后 finish
        responses = [
            {
                "tool_calls": [
                    {
                        "name": "send_chat_msg",
                        "arguments": {"room_name": "collab_room", "msg": "我开始处理任务了"}
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "name": "update_task",
                        "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "已汇报"}
                    }
                ]
            },
            {
                "tool_calls": [{"name": "finish_action", "arguments": {}}]
            }
        ]

        with self.patch_infer(responses=responses):
            await alice.task_consumer.consume()

        # 验证消息是否送达
        messages = room.messages
        assert any("我开始处理任务了" in m.content for m in messages)
        
        # 验证任务是否 DONE
        updated_task = await gtAgentTaskManager.get_task(agent_task.id)
        assert updated_task.status == TaskStatus.DONE


class TestConsumeRetryBehavior(_CollabTaskCase):
    """验证消费循环的重试逻辑。"""

    async def test_failed_todo_task_schedule_is_retried(self):
        """FAILED 状态的 TODO_TASK 调度应在下次 consume 时被重新执行并完成。"""
        team, alice_db, alice = await self._get_alice()

        agent_task = await self._create_collab_task(alice_db.id, team.id, title="重试任务")
        sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )
        await gtScheculeTaskManager.update_task_status(sched.id, AgentTaskStatus.FAILED)

        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()

        completed_sched = await gtScheculeTaskManager.get_task(sched.id)
        assert completed_sched.status == AgentTaskStatus.COMPLETED
        assert alice.task_consumer.status == AgentStatus.IDLE

        # 清理
        agent_task.status = TaskStatus.DONE
        await gtAgentTaskManager.update_task(agent_task, [GtAgentTask.status])

    async def test_failed_consumer_status_retries_todo_task(self):
        """consumer.status == FAILED 时，仍能重试 FAILED 的调度任务并完成。"""
        team, alice_db, alice = await self._get_alice()

        agent_task = await self._create_collab_task(alice_db.id, team.id, title="状态重试任务")
        sched = await gtScheculeTaskManager.create_task(
            alice_db.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )
        await gtScheculeTaskManager.update_task_status(sched.id, AgentTaskStatus.FAILED)
        alice.task_consumer.status = AgentStatus.FAILED

        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task.id, "status": "DONE", "result": "完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()

        completed_sched = await gtScheculeTaskManager.get_task(sched.id)
        assert completed_sched.status == AgentTaskStatus.COMPLETED
        assert alice.task_consumer.status == AgentStatus.IDLE

        # 清理
        agent_task.status = TaskStatus.DONE
        await gtAgentTaskManager.update_task(agent_task, [GtAgentTask.status])

    async def test_consume_processes_multiple_tasks_sequentially(self):
        """consume 应顺序处理多个 PENDING 调度，直到队列清空。"""
        team, alice_db, alice = await self._get_alice()

        agent_task1 = await self._create_collab_task(alice_db.id, team.id, title="任务1")
        sched1 = await gtScheculeTaskManager.create_task(
            alice_db.id, AgentTaskType.TODO_TASK, {"agent_task_id": agent_task1.id}
        )
        agent_task2 = await self._create_collab_task(alice_db.id, team.id, title="任务2")
        sched2 = await gtScheculeTaskManager.create_task(
            alice_db.id, AgentTaskType.TODO_TASK, {"agent_task_id": agent_task2.id}
        )

        with self.patch_infer(responses=[
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task1.id, "status": "DONE", "result": "完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
            {"tool_calls": [{"name": "update_task", "arguments": {"task_id": agent_task2.id, "status": "DONE", "result": "完成"}}]},
            {"tool_calls": [{"name": "finish_action", "arguments": {}}]},
        ]):
            await alice.task_consumer.consume()

        sched1_result = await gtScheculeTaskManager.get_task(sched1.id)
        sched2_result = await gtScheculeTaskManager.get_task(sched2.id)
        assert sched1_result.status == AgentTaskStatus.COMPLETED
        assert sched2_result.status == AgentTaskStatus.COMPLETED
        assert alice.task_consumer.status == AgentStatus.IDLE
