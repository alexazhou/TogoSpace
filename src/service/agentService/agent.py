import logging
from typing import List, Optional

from constants import AgentStatus
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentTaskConsumer import AgentTaskConsumer
from service.agentService.driver import AgentDriverConfig

logger = logging.getLogger(__name__)


class Agent:
    """AI Team Agent — facade 角色。

    Agent 本身只负责：
    - 生命周期管理（startup / close）
    - 组件装配（task_consumer）
    - 对外 API 入口（start_consumer_task, resume_failed 等）的一层转发

    Turn 级资源（driver, tool_registry, history）在 AgentTurnRunner 中，
    任务运行时状态与消费逻辑在 AgentTaskConsumer 中。
    AgentTurnRunner 由 AgentTaskConsumer 内部创建和持有。
    """


    # ─── 生命周期 ──────────────────────────────────────────────

    def __init__(
        self,
        gt_agent: GtAgent,
        system_prompt: str,
        driver_config: Optional[AgentDriverConfig] = None,
        agent_workdir: str = "",
        max_function_calls: int = 5,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.task_consumer: AgentTaskConsumer = AgentTaskConsumer(
            gt_agent=gt_agent,
            system_prompt=system_prompt,
            agent_workdir=agent_workdir,
            max_function_calls=max_function_calls,
            driver_config=driver_config,
        )

    @property
    def status(self) -> AgentStatus:
        return self.task_consumer.status

    @property
    def current_db_task(self) -> Optional[GtAgentTask]:
        return self.task_consumer.current_db_task

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃（状态为 ACTIVE 或有正在处理的任务）。"""
        return self.task_consumer.status == AgentStatus.ACTIVE or self.task_consumer.current_db_task is not None

    async def startup(self) -> None:
        await self.task_consumer._turn_runner.driver.startup()

    async def close(self) -> None:
        self.stop_consumer_task()
        await self.task_consumer._turn_runner.driver.shutdown()
        self.task_consumer._turn_runner.tool_registry.clear()

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return self.task_consumer._turn_runner._history.dump()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self.task_consumer._turn_runner._history.replace(items)


    # ─── 任务管理 ──────────────────────────────────────────────

    def start_consumer_task(self, initial_task: GtAgentTask | None = None) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        self.task_consumer.start(initial_task)

    def stop_consumer_task(self) -> None:
        """停止当前 Agent 的消费协程。"""
        self.task_consumer.stop()

    async def resume_failed(self) -> None:
        await self.task_consumer.resume_failed()
