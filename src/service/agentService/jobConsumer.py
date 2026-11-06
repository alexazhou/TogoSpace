"""JobConsumer: 调度任务管道 — 取 job、执行、状态流转、恢复失败 job。"""
from __future__ import annotations

import asyncio
import logging

from typing import Awaitable, Callable

from constants import ScheduleJobStatus, AgentStatus, AgentActivityType, AgentActivityStatus, MessageBusTopic, ScheduleJobType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtScheduleJob import GtScheduleJob
from dal.db import gtAgentTaskManager, gtScheduleJobManager
from service import messageBus, agentActivityService, taskService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from util import assertUtil, asyncUtil

logger = logging.getLogger(__name__)

#: 等待「手动 compact」结束时消费协程的轮询间隔。
#: compact 的耗时主体是一次 LLM 摘要调用（秒级），50ms 的轮询在延迟与空转间取中；
#: 这里刻意不用事件/条件变量：compact 是低频操作，多等 ≤50ms 无感知，而条件变量
#: 一旦漏掉 notify（异常路径）会让消费协程永久卡死，代价不对称。
_COMPACTING_POLL_INTERVAL_SECONDS = 0.05


class JobConsumer:
    """调度任务管道：认领 → 执行 → 状态流转。合并了原 AgentTaskExecutor 的职责。

    自行构建 AgentTurnRunner，对外只暴露任务消费接口。
    """

    def __init__(
        self,
        *,
        gt_agent: GtAgent,
        system_prompt: str,
        agent_workdir: str = "",
        driver_config: AgentDriverConfig | None = None,
        prompt_refresh_fn: Callable[[], Awaitable[str]] | None = None,
    ):
        self.gt_agent: GtAgent = gt_agent
        self._turn_runner: AgentTurnRunner = AgentTurnRunner(
            gt_agent=gt_agent,
            system_prompt=system_prompt,
            agent_workdir=agent_workdir,
            driver_config=driver_config,
            prompt_refresh_fn=prompt_refresh_fn,
        )
        self.status: AgentStatus = AgentStatus.IDLE
        self._aio_consumer_task: asyncio.Task | None = None
        self._cancel_requested: bool = False

    @property
    def agent_workdir(self) -> str:
        return self._turn_runner.agent_workdir

    async def _set_status(self, status: AgentStatus, error_message: str | None = None) -> None:
        """统一处理 Agent 状态切换：更新运行时状态、广播事件并记录活动。"""
        if self.status == status:
            return
        self.status = status
        messageBus.publish(MessageBusTopic.AGENT_STATUS_CHANGED, gt_agent=self.gt_agent, status=status)
        await agentActivityService.add_activity(
            gt_agent=self.gt_agent, activity_type=AgentActivityType.AGENT_STATE,
            status=AgentActivityStatus.SUCCEEDED, detail=status.name, error_message=error_message,
        )

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

    def cancel_current_turn(self) -> bool:
        """人工停止当前 turn。返回 True 表示已发出取消信号，False 表示当前不可取消。"""
        if self.status != AgentStatus.ACTIVE:
            logger.info(f"取消请求被忽略（非 ACTIVE 状态）: {self.gt_agent.name}(agent_id={self.gt_agent.id}), status={self.status.name}")
            return False
        task = self._aio_consumer_task
        if task is None or task.done():
            logger.info(f"取消请求被忽略（消费协程不存在或已结束）: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
            return False
        self._cancel_requested = True
        task.cancel()
        logger.info(f"已发出取消信号: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
        return True

    async def _check_and_schedule_collaboration_tasks(self) -> None:
        """扫描协作任务表，若有待处理任务且无对应 PENDING 调度记录，则自动创建。"""
        agent_task = await gtAgentTaskManager.get_first_active_task(self.gt_agent.id)
        if agent_task is None:
            return

        already_scheduled = await gtScheduleJobManager.has_pending_collaboration_job(
            self.gt_agent.id, agent_task.id
        )
        if already_scheduled:
            logger.debug(f"协作任务已有 PENDING 调度记录，跳过: {self.gt_agent.name}(agent_id={self.gt_agent.id}), agent_task_id={agent_task.id}")
            return

        logger.info(f"自动创建协作任务调度: {self.gt_agent.name}(agent_id={self.gt_agent.id}), agent_task_id={agent_task.id}, title={agent_task.title!r}")
        await gtScheduleJobManager.create_job(
            self.gt_agent.id,
            ScheduleJobType.TODO_TASK,
            {"agent_task_id": agent_task.id},
        )

    async def _claim_collaboration_task(self, agent_task_id: int) -> None:
        """认领置位（D5=(a)）：把 assignee 名下 `TODO` 的协作任务置为 `IN_PROGRESS`。

        系统操作、幂等、不唤醒 / 不通知；异常只记录、不阻断任务执行。
        """
        try:
            await taskService.claim_task(self.gt_agent.id, agent_task_id)
        except Exception:
            logger.exception(
                f"认领置位失败（不影响任务执行）: agent_id={self.gt_agent.id}, agent_task_id={agent_task_id}"
            )

    # ─── 消费循环 ─────────────────────────────────────────────
    async def consume(self) -> None:
        """从数据库获取并处理任务，直到没有待处理任务为止。"""
        self._cancel_requested = False  # 防御性重置

        current_consumer = asyncio.current_task()
        if current_consumer is not None and self._aio_consumer_task not in (None, current_consumer):
            existing = self._aio_consumer_task
            assert existing is None or existing.done(), (
                f"消费协程重入: {self.gt_agent.name}(agent_id={self.gt_agent.id}), "
                f"existing_task={id(existing)}, current_task={id(current_consumer)}"
            )

        while True:
            # 与「手动 compact」互斥：compact 会 `reload_from_db()` 整体覆盖 `_items`，
            # 这期间若开跑 turn，turn 在内存里 append 的消息会被旧快照抹掉（并进而
            # 让 `_next_seq()` 与库里已占用的 seq 撞号、以及两次 compact 算出同一个
            # `insert_seq` 后互相覆盖）。
            #
            # 等待放在 `_set_status(ACTIVE)` **之前**：此刻并没有 turn 在跑，状态就该
            # 如实保持 IDLE，不让面板显示「忙碌」。这段窗口里点「停止」会拿到
            # `agent_not_active`——与基线里「job 已落库但 turn 尚未开始」同构，
            # 不是新引入的语义。
            # 用 `is True` 而不是真值判断：runner 被替身/Mock 顶替时属性是任意对象，
            # 真值判断会把它当「一直在压缩」而死等。
            while self._turn_runner.is_compacting is True:
                await asyncio.sleep(_COMPACTING_POLL_INTERVAL_SECONDS)

            await self._set_status(AgentStatus.ACTIVE)
            job = await gtScheduleJobManager.get_first_unfinished_job(self.gt_agent.id)

            logger.info(f"检查待处理任务: {self.gt_agent.name}(agent_id={self.gt_agent.id})")

            if job is None:
                logger.info(f"无待处理任务，退出消费循环: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
                break

            if job.status not in (ScheduleJobStatus.PENDING, ScheduleJobStatus.RUNNING, ScheduleJobStatus.FAILED):
                logger.info(f"首个未完成任务状态不可消费，退出消费循环: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={job.id}, job_status={job.status}")
                break

            if job.status in (ScheduleJobStatus.PENDING, ScheduleJobStatus.FAILED):
                claimed_job = await gtScheduleJobManager.transition_job_status(job.id, job.status, ScheduleJobStatus.RUNNING)
                if claimed_job is None:
                    logger.debug(f"任务认领失败（已被其他消费者抢占），重试: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={job.id}")
                    continue
                if job.status == ScheduleJobStatus.FAILED:
                    logger.info(f"重跑 FAILED 任务: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={job.id}")
            else:
                claimed_job = job  # 已经是 RUNNING，直接使用

            # 认领置位（D5=(a)）：执行人认领协作任务时，系统把 TODO → IN_PROGRESS（同事务写系统事件，不唤醒）
            if claimed_job.job_type == ScheduleJobType.TODO_TASK:
                agent_task_id = (claimed_job.task_data or {}).get("agent_task_id")
                if agent_task_id:
                    await self._claim_collaboration_task(agent_task_id)

            logger.info(f"开始执行任务: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={claimed_job.id}")

            try:
                await self._turn_runner.run_task_turn(claimed_job)
            except asyncio.CancelledError:
                if not self._cancel_requested:
                    raise  # 非人工停止（hot reload / 服务关闭），保持原有穿透行为
                self._cancel_requested = False
                logger.info(f"Agent 任务被人工停止: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={claimed_job.id}")
                await self._turn_runner.handle_cancel_turn()
                await gtScheduleJobManager.update_job_status(claimed_job.id, ScheduleJobStatus.CANCELLED, error_message="cancelled by user")
                await agentActivityService.add_activity(
                    gt_agent=self.gt_agent, activity_type=AgentActivityType.AGENT_STATE,
                    status=AgentActivityStatus.CANCELLED, detail="Turn 被操作者停止",
                )
                break
            except Exception as e:
                logger.error(f"Agent 任务执行失败: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={claimed_job.id}, error={e}")
                await gtScheduleJobManager.update_job_status(claimed_job.id, ScheduleJobStatus.FAILED, error_message=str(e))
                await self._set_status(AgentStatus.FAILED, str(e))
                break

            logger.info(f"任务执行完成: {self.gt_agent.name}(agent_id={self.gt_agent.id}), job_id={claimed_job.id}")
            await gtScheduleJobManager.update_job_status(claimed_job.id, ScheduleJobStatus.COMPLETED)
            await self._check_and_schedule_collaboration_tasks()

        # 清理逻辑
        if self.status != AgentStatus.FAILED:
            await self._set_status(AgentStatus.IDLE)
            logger.info(f"消费循环结束，状态回到 IDLE: {self.gt_agent.name}(agent_id={self.gt_agent.id})")

        if self._aio_consumer_task is current_consumer:
            self._aio_consumer_task = None
            if self.status != AgentStatus.FAILED:
                has_pending = await gtScheduleJobManager.has_consumable_job(self.gt_agent.id)
                if has_pending:
                    logger.info(f"Agent 任务收尾时检测到待处理任务，自动续起消费: {self.gt_agent.name}(agent_id={self.gt_agent.id})")
                    self.start()
