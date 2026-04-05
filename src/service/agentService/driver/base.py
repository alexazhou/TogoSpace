from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from constants import DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.toolRegistry import AgentToolRegistry
from util import llmApiUtil


@dataclass
class AgentDriverConfig:
    driver_type: DriverType = DriverType.NATIVE
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurnActionResult:
    ok: bool
    message: str
    turn_finished: bool = False


@dataclass
class AgentTurnSetup:
    max_retries: int = 1
    hint_prompt: str = ""


class AgentDriverHost(Protocol):
    gt_agent: GtAgent
    system_prompt: str
    agent_workdir: str
    _history: AgentHistoryStore
    tool_registry: AgentToolRegistry

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        ...

    async def _execute_tool(self) -> None:
        ...


class AgentDriver:
    def __init__(self, host: AgentDriverHost, config: AgentDriverConfig):
        self.host = host
        self.config = config
        self._started: bool = False

    @property
    def driver_type(self) -> DriverType:
        return self.config.driver_type

    @property
    def started(self) -> bool:
        return self._started

    @property
    def host_managed_turn_loop(self) -> bool:
        return False

    async def startup(self) -> None:
        self._started = True

    async def shutdown(self) -> None:
        self._started = False

    @property
    def turn_setup(self) -> AgentTurnSetup:
        return AgentTurnSetup()

    async def run_chat_turn(self, task: GtAgentTask, synced_count: int) -> None:
        raise NotImplementedError
