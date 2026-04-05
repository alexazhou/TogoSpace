"""AgentTaskConsumer: 任务管道 — 取任务、执行、状态流转、恢复失败任务。"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from constants import AgentTaskStatus, AgentStatus
from model.dbModel.gtAgentTask import GtAgentTask
from dal.db import gtAgentTaskManager
from service import roomService

if TYPE_CHECKING:
    from service.agentService.agent import Agent

logger = logging.getLogger(__name__)


class AgentTaskConsumer:
    """任务管道：认领 → 执行 → 状态流转。合并了原 AgentTaskExecutor 的职责。"""

    def __init__(self, agent: Agent):
        self._agent = agent

    # ─── 消费循环 ─────────────────────────────────────────────

    async def consume(
        self,
        max_function_calls: int | None = None,
        initial_task: GtAgentTask | None = None,
    ) -> None:
        """从数据库获取并处理任务，直到没有待处理任务为止。"""
        agent = self._agent
        current_consumer = asyncio.current_task()
        if current_consumer is not None and agent.consumer_task not in (None, current_consumer):
            existing = agent.consumer_task
            if existing.done() is False:
                logger.warning(f"检测到重复启动的消费协程: agent_id={agent.gt_agent.id}, existing_task={id(existing)}, current_task={id(current_consumer)}")
        effective_max_fc = agent.max_function_calls if max_function_calls is None else max(1, max_function_calls)
        if agent.status != AgentStatus.ACTIVE:
            agent.status = AgentStatus.ACTIVE
            agent._publish_status(agent.status)
        try:
            claimed_task = initial_task
            resumed = initial_task is not None
            while True:
                if claimed_task is None:
                    task = await gtAgentTaskManager.get_first_unfinish_task(agent.gt_agent.id)
                    if task is None:
                        break
                    if task.status != AgentTaskStatus.PENDING:
                        break

                    claimed_task = await gtAgentTaskManager.transition_task_status(task.id, AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING)
                    if claimed_task is None:
                        continue

                completed = await self._execute_task(
                    claimed_task,
                    effective_max_fc,
                    resumed=resumed,
                )
                if completed is False:
                    return
                claimed_task = None
                resumed = False
        finally:
            if agent.status != AgentStatus.FAILED:
                agent.status = AgentStatus.IDLE
                agent._publish_status(agent.status)

            if agent.consumer_task is current_consumer:
                agent.consumer_task = None
                if agent.status == AgentStatus.FAILED:
                    return
                has_pending = await gtAgentTaskManager.has_consumable_task(agent.gt_agent.id)
                if has_pending:
                    logger.info(f"Agent 任务收尾时检测到待处理任务，自动续起消费: agent_id={agent.gt_agent.id}")
                    agent.start_consumer_task()

    # ─── 单任务执行（原 AgentTaskExecutor.execute） ───────────

    async def _execute_task(self, claimed_task: GtAgentTask, max_function_calls: int, *, resumed: bool) -> bool:
        """执行一条已处于 RUNNING 状态的任务。

        返回 True 表示任务完成，可继续后续任务；返回 False 表示任务失败，消费流程应立即停止。
        """
        agent = self._agent
        agent.current_db_task = claimed_task
        try:
            await agent.turn_runner.run_chat_turn(claimed_task, max_function_calls, resumed=resumed)
        except Exception as e:
            room_id = claimed_task.task_data.get("room_id")
            room = roomService.get_room(room_id) if room_id is not None else None
            room_key = room.key if room is not None else f"room_id={room_id}"
            logger.error(f"Agent 任务执行失败并标记为 FAILED: agent_id={agent.gt_agent.id}, room={room_key}, task={claimed_task.id}, error={e}")
            await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.FAILED, error_message=str(e))
            agent.status = AgentStatus.FAILED
            agent.current_db_task = None
            agent._publish_status(agent.status)
            return False

        await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.COMPLETED)
        agent.current_db_task = None
        return True

    # ─── 恢复失败任务 ────────────────────────────────────────

    async def resume_failed(self) -> int:
        """恢复最早的 FAILED 任务，并重新启动消费。"""
        agent = self._agent
        failed_task = await gtAgentTaskManager.get_first_unfinish_task(agent.gt_agent.id)
        if failed_task is None or failed_task.status != AgentTaskStatus.FAILED:
            raise RuntimeError(f"no failed task to resume: agent_id={agent.gt_agent.id}")

        room_id = failed_task.task_data.get("room_id")
        if room_id is None:
            raise RuntimeError(f"failed task missing room_id: agent_id={agent.gt_agent.id}, task_id={failed_task.id}")

        resumed_task = await gtAgentTaskManager.transition_task_status(failed_task.id, AgentTaskStatus.FAILED, AgentTaskStatus.RUNNING)
        if resumed_task is None:
            raise RuntimeError(f"failed task resume conflict: agent_id={agent.gt_agent.id}, task_id={failed_task.id}")

        agent.status = AgentStatus.ACTIVE
        agent._publish_status(agent.status)
        agent.start_consumer_task(initial_task=resumed_task)
        return room_id
