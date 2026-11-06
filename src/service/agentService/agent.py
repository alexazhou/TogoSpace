import logging
from typing import Awaitable, Callable, List, Optional

from constants import AgentStatus, ContainerState
from model.coreModel.agentModel import AgentContextUsage, ExecEnvState
from model.coreModel.containerModel import ContainerTarget
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.compact import CompactInProgressError
from service.agentService.jobConsumer import JobConsumer
from service.agentService.driver import (
    AgentDriver,
    AgentDriverConfig,
)

logger = logging.getLogger(__name__)


class Agent:
    """AI Team Agent — facade 角色。

    Agent 本身负责：
    - 生命周期管理（startup / close）
    - 组件装配（job_consumer）
    - 执行环境接口转发（enter_container / exit_to_host 委托给 Driver）
    - 对外 API 入口（start_consumer_task 等）的一层转发

    Turn 级资源（driver, tool_registry, history）在 AgentTurnRunner 中，
    任务运行时状态与消费逻辑在 JobConsumer 中。
    AgentTurnRunner 由 JobConsumer 内部创建和持有。
    """


    # ─── 生命周期 ──────────────────────────────────────────────

    def __init__(
        self,
        gt_agent: GtAgent,
        system_prompt: str,
        driver_config: Optional[AgentDriverConfig] = None,
        agent_workdir: str = "",
        is_root_leader: bool = False,
        prompt_refresh_fn: Optional[Callable[[], Awaitable[str]]] = None,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.is_root_leader: bool = is_root_leader
        self.job_consumer: JobConsumer = JobConsumer(
            gt_agent=gt_agent,
            system_prompt=system_prompt,
            agent_workdir=agent_workdir,
            driver_config=driver_config,
            prompt_refresh_fn=prompt_refresh_fn,
        )

    @property
    def driver(self) -> AgentDriver:
        return self.job_consumer._turn_runner.driver

    @property
    def status(self) -> AgentStatus:
        return self.job_consumer.status

    @property
    def agent_workdir(self) -> str:
        """Agent 在宿主上的私有工作目录（容器内对应 `/workspace/self`）。"""
        return self.job_consumer.agent_workdir

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃。"""
        return self.job_consumer.status == AgentStatus.ACTIVE

    @property
    def host_managed_turn_loop(self) -> bool:
        """是否使用 host-managed turn loop（支持运行中即时消息插入）。"""
        return self.driver.host_managed_turn_loop

    async def startup(self) -> None:
        await self.driver.startup()

    async def close(self) -> None:
        self.job_consumer.status = AgentStatus.CLOSED
        self.stop_consumer_task()
        await self.driver.shutdown()
        self.job_consumer._turn_runner.tool_registry.clear()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self.job_consumer._turn_runner._history.replace(items)

    # ─── 上下文用量 ────────────────────────────────────────────

    def snapshot_context_usage(self) -> AgentContextUsage | None:
        """当前上下文占用快照；算不出来时 `None`（前端整条不渲染）。

        走内存态 history 而不是另读一次 DB：内存与库在同一时刻可能不一致
        （推理刚追加、尚未落盘），面板要反映的是「此刻这一轮实际在用的上下文」。
        """
        return self.job_consumer._turn_runner.snapshot_context_usage()

    @property
    def is_compacting(self) -> bool:
        """是否正在执行手动 compact（接口层据此拒绝重复请求）。"""
        return self.job_consumer._turn_runner.is_compacting

    async def compact_context(self) -> AgentContextUsage | None:
        """手动压缩：执行 compact 并返回压缩后的新用量。

        `NoCompactableHistoryError` 原样上抛——接口层要据此回 400
        `no_compactable_history` 而不是 500，翻译错误语义是接口层的职责，
        facade 不替它做决定。

        ## 临界区在这里占用，不往内层放

        `try_acquire_manual_compact()` 是「检查并占用」的同步动作。它必须与接口层
        的前置检查（`compact_in_progress`）落在**同一个不让出事件循环的同步段**里，
        两个并发请求才不会双双通过检查。

        ⚠️ **`try_acquire_manual_compact()` 这一行之前、以及本方法在 await
        `execute_manual_compact()` 之前的任何位置，都不得插入 `await`。**
        插入 await 会让出控制权，「检查 → 占用」不再原子，并发防护**静默**失效
        （另一请求会在置位前通过检查并一起进入 compact）。这条约束有对应的
        并发测试守着：删掉防护后「第二次请求应得 400」那条断言会红。

        占用失败即抛 `CompactInProgressError`，**拒绝而不是排队**：排队会把用户
        连点两次变成串行压两次，用户看到的仍是「点了两次、摘要被压了两遍」。

        ## 压缩不可取消

        全程不碰 `status`（保持 IDLE），所以 `cancel_current_turn()` 会如实返回
        False、接口层报 `agent_not_active`。这不是缺陷也不是遗漏：压缩的耗时主体是
        一次 LLM 摘要调用，中途打断会留下「摘要已插库但内存未裁剪」的半成品，
        比让它跑完更危险。本单不提供取消能力，前端按钮在压缩期间禁用。
        """
        if not self.job_consumer._turn_runner.try_acquire_manual_compact():
            raise CompactInProgressError("另一个手动压缩正在进行")
        try:
            # 纯转发，无本层 await——见上面对「禁止插入 await」的说明。
            return await self.job_consumer._turn_runner.execute_manual_compact()
        finally:
            self.job_consumer._turn_runner.release_manual_compact()

    # ─── 执行环境 ──────────────────────────────────────────────

    def get_exec_env_state(self) -> ExecEnvState:
        """读取当前执行环境；不支持容器的驱动恒为宿主。"""
        return self.driver.get_exec_env()

    async def enter_container(self, container_id: str) -> tuple[ContainerTarget, ContainerState]:
        """把 Agent 的执行环境切换到指定容器，返回 `(目标, 进入前状态)`。

        环境切换由 Driver 负责；基类对不支持容器的驱动显式报错，
        TSP Driver 负责解析目标、校验状态、注入 gtsp 与连接切换。
        """
        return await self.driver.enter_container(container_id)

    async def exit_to_host(self) -> bool:
        """把 Agent 的执行环境切回宿主（§5.2）；具体切换由 Driver 负责。"""
        return await self.driver.exit_to_host()

    # ─── 任务管理 ──────────────────────────────────────────────

    def start_consumer_task(self) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        if self.status == AgentStatus.CLOSED:
            logger.info(f"Agent {self.gt_agent.name}(agent_id={self.gt_agent.id}) 已经被关闭，忽略调度唤醒")
            return
        self.job_consumer.start()

    def stop_consumer_task(self) -> None:
        """停止当前 Agent 的消费协程。"""
        self.job_consumer.stop()

    def cancel_current_turn(self) -> bool:
        """人工停止当前 turn。返回 True 表示已发出取消信号。"""
        return self.job_consumer.cancel_current_turn()
