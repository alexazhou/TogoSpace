"""AgentTaskConsumer: 任务管道 — 取任务、执行、状态流转、恢复失败任务。"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from constants import AgentTaskStatus, AgentStatus, MessageBusTopic
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from dal.db import gtAgentTaskManager
from service import messageBus
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from util import assertUtil, asyncUtil

logger = logging.getLogger(__name__)


class AgentTaskConsumer:
    """任务管道：认领 → 执行 → 状态流转。合并了原 AgentTaskExecutor 的职责。

    自行构建 AgentTurnRunner，对外只暴露任务消费接口。
    """

    def __init__(
        self,
        *,
        gt_agent: GtAgent,
        system_prompt: str,
        agent_workdir: str = "",
        max_function_calls: int = 5,
        driver_config: AgentDriverConfig | None = None,
    ):
        self.gt_agent: GtAgent = gt_agent
        self._turn_runner: AgentTurnRunner = AgentTurnRunner(
            gt_agent=gt_agent,
            system_prompt=system_prompt,
            agent_workdir=agent_workdir,
            max_function_calls=max_function_calls,
            driver_config=driver_config,
        )
        self.status: AgentStatus = AgentStatus.IDLE
        self._aio_consumer_task: asyncio.Task | None = None
        self.current_db_task: Optional[GtAgentTask] = None

    def _publish_status(self, status: AgentStatus) -> None:
        messageBus.publish(MessageBusTopic.AGENT_STATUS_CHANGED, gt_agent=self.gt_agent, status=status)

    def start(self, initial_task: GtAgentTask | None = None) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        existing = self._aio_consumer_task
        if existing is not None and not existing.done():
            return
        self._aio_consumer_task = asyncio.create_task(self.consume(initial_task=initial_task))

    def stop(self) -> None:
        """停止消费协程。"""
        task = self._aio_consumer_task
        self._aio_consumer_task = None
        asyncUtil.cancel_task_safely(task)

    # ─── 消费循环 ─────────────────────────────────────────────
    async def consume(self, initial_task: GtAgentTask | None = None) -> None:
        """从数据库获取并处理任务，直到没有待处理任务为止。"""
        current_consumer = asyncio.current_task()
        if current_consumer is not None and self._aio_consumer_task not in (None, current_consumer):
            existing = self._aio_consumer_task
            if existing.done() is False:
                logger.warning(f"检测到重复启动的消费协程: agent_id={self.gt_agent.id}, existing_task={id(existing)}, current_task={id(current_consumer)}")

        if self.status != AgentStatus.ACTIVE:
            self.status = AgentStatus.ACTIVE
            self._publish_status(self.status)

        claimed_task = initial_task
        resumed = initial_task is not None
        while True:
            if claimed_task is None:
                task = await gtAgentTaskManager.get_first_unfinish_task(self.gt_agent.id)
                if task is None:
                    break
                if task.status != AgentTaskStatus.PENDING:
                    break

                claimed_task = await gtAgentTaskManager.transition_task_status(task.id, AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING)
                if claimed_task is None:
                    continue

            completed = await self._execute_task(claimed_task, resumed=resumed)
            if completed is False:
                break
            claimed_task = None
            resumed = False

        # 清理逻辑
        if self.status != AgentStatus.FAILED:
            self.status = AgentStatus.IDLE
            self._publish_status(self.status)

        if self._aio_consumer_task is current_consumer:
            self._aio_consumer_task = None
            if self.status != AgentStatus.FAILED:
                has_pending = await gtAgentTaskManager.has_consumable_task(self.gt_agent.id)
                if has_pending:
                    logger.info(f"Agent 任务收尾时检测到待处理任务，自动续起消费: agent_id={self.gt_agent.id}")
                    self.start()

    # ─── 单任务执行（原 AgentTaskExecutor.execute） ───────────
    async def _execute_task(self, claimed_task: GtAgentTask, *, resumed: bool) -> bool:
        """执行一条已处于 RUNNING 状态的任务。

        返回 True 表示任务完成，可继续后续任务；返回 False 表示任务失败，消费流程应立即停止。
        """
        self.current_db_task = claimed_task
        try:
            await self._turn_runner.run_chat_turn(claimed_task, resumed=resumed)
        except Exception as e:
            logger.error(f"Agent 任务执行失败: agent_id={self.gt_agent.id}, task={claimed_task.id}, error={e}")
            await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.FAILED, error_message=str(e))
            self.status = AgentStatus.FAILED
            self.current_db_task = None
            self._publish_status(self.status)
            return False

        await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.COMPLETED)
        self.current_db_task = None
        return True

    # ─── 恢复失败任务 ────────────────────────────────────────
    async def resume_failed(self) -> None:
        """恢复最早的 FAILED 任务，并重新启动消费。"""
        failed_task = await gtAgentTaskManager.get_first_unfinish_task(self.gt_agent.id)
        assertUtil.assertNotNull(failed_task, error_message=f"no failed task to resume: agent_id={self.gt_agent.id}")
        assertUtil.assertEqual(failed_task.status, AgentTaskStatus.FAILED, error_message=f"task is not FAILED: agent_id={self.gt_agent.id}")

        resumed_task = await gtAgentTaskManager.transition_task_status(failed_task.id, AgentTaskStatus.FAILED, AgentTaskStatus.RUNNING)
        assertUtil.assertNotNull(resumed_task, error_message=f"failed task resume conflict: agent_id={self.gt_agent.id}, task_id={failed_task.id}")

        self.status = AgentStatus.ACTIVE
        self._publish_status(self.status)
        self.start(initial_task=resumed_task)
