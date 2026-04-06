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

    def start(self) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        existing = self._aio_consumer_task
        if existing is not None and not existing.done():
            logger.debug(f"消费协程已在运行，跳过启动: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
            return
        logger.info(f"启动消费协程: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
        self._aio_consumer_task = asyncio.create_task(self.consume())

    def stop(self) -> None:
        """停止消费协程。"""
        task = self._aio_consumer_task
        self._aio_consumer_task = None
        if task is not None:
            logger.info(f"停止消费协程: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_done={task.done()}")
        asyncUtil.cancel_task_safely(task)

    # ─── 消费循环 ─────────────────────────────────────────────
    async def consume(self) -> None:
        """从数据库获取并处理任务，直到没有待处理任务为止。"""
        current_consumer = asyncio.current_task()
        if current_consumer is not None and self._aio_consumer_task not in (None, current_consumer):
            existing = self._aio_consumer_task
            assert existing is None or existing.done(), (
                f"消费协程重入: {self.gt_agent.name}(agent_id={self.gt_agent.id}), "
                f"existing_task={id(existing)}, current_task={id(current_consumer)}"
            )

        if self.status != AgentStatus.ACTIVE:
            self.status = AgentStatus.ACTIVE
            self._publish_status(self.status)

        logger.info(f"进入消费循环: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
        while True:
            task = await gtAgentTaskManager.get_first_unfinish_task(self.gt_agent.id)

            if task is None:
                logger.info(f"无待处理任务，退出消费循环: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
                break

            if task.status not in (AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING):
                logger.info(f"首个未完成任务非 PENDING/RUNNING，退出消费循环: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={task.id}, task_status={task.status}")
                break

            if task.status == AgentTaskStatus.PENDING:
                claimed_task = await gtAgentTaskManager.transition_task_status(task.id, AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING)
                if claimed_task is None:
                    logger.debug(f"任务认领失败（已被其他消费者抢占），重试: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={task.id}")
                    continue
            else:
                claimed_task = task  # 已经是 RUNNING，直接使用

            resumed = self._turn_runner._history.has_unfinished_turn()
            completed = await self._execute_task(claimed_task, resumed=resumed)
            if completed is False:
                break

        # 清理逻辑
        if self.status != AgentStatus.FAILED:
            self.status = AgentStatus.IDLE
            self._publish_status(self.status)
            logger.info(f"消费循环结束，状态回到 IDLE: {self.gt_agent.name}(agent_id={self.gt_agent.id})")

        if self._aio_consumer_task is current_consumer:
            self._aio_consumer_task = None
            if self.status != AgentStatus.FAILED:
                has_pending = await gtAgentTaskManager.has_consumable_task(self.gt_agent.id)
                if has_pending:
                    logger.info(f"Agent 任务收尾时检测到待处理任务，自动续起消费: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
                    self.start()

    # ─── 单任务执行（原 AgentTaskExecutor.execute） ───────────
    async def _execute_task(self, claimed_task: GtAgentTask, *, resumed: bool) -> bool:
        """执行一条已处于 RUNNING 状态的任务。

        返回 True 表示任务完成，可继续后续任务；返回 False 表示任务失败，消费流程应立即停止。
        """
        self.current_db_task = claimed_task
        logger.info(f"开始执行任务: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={claimed_task.id}, resumed={resumed}")

        try:
            await self._turn_runner.run_chat_turn(claimed_task, resumed=resumed)
        except Exception as e:
            logger.error(f"Agent 任务执行失败: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={claimed_task.id}, error={e}")
            await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.FAILED, error_message=str(e))
            self.status = AgentStatus.FAILED
            self.current_db_task = None
            self._publish_status(self.status)
            return False

        logger.info(f"任务执行完成: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={claimed_task.id}")
        await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.COMPLETED)
        self.current_db_task = None
        return True

    # ─── 恢复失败任务 ────────────────────────────────────────
    async def resume_failed(self) -> None:
        """恢复最早的 FAILED 任务，并重新启动消费。"""
        failed_task = await gtAgentTaskManager.get_first_unfinish_task(self.gt_agent.id)
        assertUtil.assertNotNull(failed_task, error_message=f"no failed task to resume: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
        assertUtil.assertEqual(failed_task.status, AgentTaskStatus.FAILED, error_message=f"task is not FAILED: {self.gt_agent.name}(agent_id={self.gt_agent.id})")

        resumed_task = await gtAgentTaskManager.transition_task_status(failed_task.id, AgentTaskStatus.FAILED, AgentTaskStatus.RUNNING)
        assertUtil.assertNotNull(resumed_task, error_message=f"failed task resume conflict: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={failed_task.id}")

        logger.info(f"恢复失败任务: {self.gt_agent.name}(agent_id={self.gt_agent.id}), task_id={failed_task.id}")
        self.start()
