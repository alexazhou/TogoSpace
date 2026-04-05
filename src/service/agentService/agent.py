import logging
from typing import List, Optional

from constants import DriverType, AgentStatus
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.agentTaskConsumer import AgentTaskConsumer
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig, build_agent_driver
from service.agentService.toolRegistry import AgentToolRegistry
from util import llmApiUtil

logger = logging.getLogger(__name__)


class Agent:
    """AI Team Agent — facade 角色。

    Agent 本身只负责：
    - 生命周期管理（startup / close）
    - 组件装配（driver, turn_runner, task_consumer）
    - 对外 API 入口（start_consumer_task, resume_failed 等）的一层转发
    - AgentDriverHost 协议（供 driver 回调）

    任务运行时状态与消费逻辑在 AgentTaskConsumer 中，
    Turn 级推理与工具调用逻辑在 AgentTurnRunner 中。
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
        self.agent_workdir: str = agent_workdir
        self._history_store: AgentHistoryStore = AgentHistoryStore(self.gt_agent.id)
        self._tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))
        self.turn_runner: AgentTurnRunner = AgentTurnRunner(self, max_function_calls=max_function_calls)
        self.task_consumer: AgentTaskConsumer = AgentTaskConsumer(
            gt_agent=self.gt_agent,
            turn_runner=self.turn_runner,
        )

    @property
    def status(self) -> AgentStatus:
        return self.task_consumer.status

    @property
    def current_db_task(self) -> Optional[GtAgentTask]:
        return self.task_consumer.current_db_task

    @property
    def _history(self) -> AgentHistoryStore:
        return self._history_store

    @property
    def tool_registry(self) -> AgentToolRegistry:
        return self._tool_registry

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃（状态为 ACTIVE 或有正在处理的任务）。"""
        return self.task_consumer.status == AgentStatus.ACTIVE or self.task_consumer.current_db_task is not None

    async def startup(self) -> None:
        await self.driver.startup()

    async def close(self) -> None:
        self.stop_consumer_task()
        await self.driver.shutdown()
        self.tool_registry.clear()

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return self._history.dump()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self._history.replace(items)


    # ─── 任务管理 ──────────────────────────────────────────────

    def start_consumer_task(self, initial_task: GtAgentTask | None = None) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        self.task_consumer.start(initial_task)

    def stop_consumer_task(self) -> None:
        """停止当前 Agent 的消费协程。"""
        self.task_consumer.stop()

    async def resume_failed(self) -> None:
        await self.task_consumer.resume_failed()


    # ─── AgentDriverHost 协议 ───────────────────────────────────
    # Driver 通过 self.host 回调以下方法，Agent 必须保留这些入口。

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        return await self.turn_runner._infer(tools)

    async def _execute_tool(self) -> None:
        await self.turn_runner._execute_tool()
