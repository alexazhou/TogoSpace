import logging
from typing import List, Optional

from constants import AgentStatus, ContainerState
from model.coreModel.agentModel import ExecEnvState
from model.coreModel.containerModel import ContainerTarget
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
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
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.is_root_leader: bool = is_root_leader
        self.job_consumer: JobConsumer = JobConsumer(
            gt_agent=gt_agent,
            system_prompt=system_prompt,
            agent_workdir=agent_workdir,
            driver_config=driver_config,
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
