import asyncio
import logging
from typing import List, Optional

from constants import DriverType, MessageBusTopic, AgentStatus
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import messageBus
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.agentTaskConsumer import AgentTaskConsumer
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig, build_agent_driver
from service.agentService.toolRegistry import AgentToolRegistry
from util import asyncUtil, llmApiUtil

logger = logging.getLogger(__name__)


class Agent:
    """AI Team Agent — 协调器角色。

    Agent 本身只负责：
    - 运行时属性与生命周期（startup / close）
    - 组件装配（driver, turn_runner, task_consumer）
    - 对外 API 入口（consume_task, resume_failed, run_chat_turn 等）
    - 状态广播

    实际的任务消费与执行逻辑在 AgentTaskConsumer 中，
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
        self.max_function_calls: int = max(1, max_function_calls)
        self._history_store: AgentHistoryStore = AgentHistoryStore(self.gt_agent.id or 0)
        self._tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.status: AgentStatus = AgentStatus.IDLE
        self._aio_consumer_task: asyncio.Task | None = None
        self.current_db_task: Optional[GtAgentTask] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))
        self.turn_runner: AgentTurnRunner = AgentTurnRunner(self)
        self.task_consumer: AgentTaskConsumer = AgentTaskConsumer(self)

    @property
    def _history(self) -> AgentHistoryStore:
        return self._history_store

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃（状态为 ACTIVE 或有正在处理的任务）。"""
        return self.status == AgentStatus.ACTIVE or self.current_db_task is not None

    async def startup(self) -> None:
        await self.driver.startup()

    async def close(self) -> None:
        self.stop_consumer_task()
        await self.driver.shutdown()
        self._tool_registry.clear()

    def _publish_status(self, status: AgentStatus) -> None:
        messageBus.publish(MessageBusTopic.AGENT_STATUS_CHANGED, gt_agent=self.gt_agent, status=status)

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return self._history.dump()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self._history.replace(items)


    # ─── 任务管理 ──────────────────────────────────────────────

    def start_consumer_task(self, initial_task: GtAgentTask | None = None) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        existing = self._aio_consumer_task
        if existing is not None and not existing.done():
            return

        self._aio_consumer_task = asyncio.create_task(self.task_consumer.consume(initial_task=initial_task))

    def stop_consumer_task(self) -> None:
        """停止当前 Agent 的消费协程。"""
        task = self._aio_consumer_task
        self._aio_consumer_task = None
        asyncUtil.cancel_task_safely(task)

    async def resume_failed(self) -> None:
        await self.task_consumer.resume_failed()


    # ─── AgentDriverHost 协议 ───────────────────────────────────
    # Driver 通过 self.host 回调以下方法，Agent 必须保留这些入口。

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        return await self.turn_runner._infer(tools)

    async def _execute_tool(self) -> None:
        await self.turn_runner._execute_tool()
