from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from constants import DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.toolRegistry import AgentToolRegistry


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

    async def _execute_tool(self) -> None:
        ...


class AgentDriver:
    def __init__(self, host: AgentDriverHost, config: AgentDriverConfig):
        self.host = host
        self.config = config
        self._started: bool = False
        self._startup_loop: asyncio.AbstractEventLoop | None = None

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
        self._startup_loop = asyncio.get_running_loop()

    async def shutdown(self) -> None:
        if self._startup_loop is not None:
            current_loop = asyncio.get_running_loop()
            assert current_loop is self._startup_loop, (
                f"AgentDriver.shutdown() 必须在 startup() 所用的事件循环上调用，"
                f"否则 asyncio IO 对象无法正常关闭。"
                f"startup_loop={id(self._startup_loop)}, current_loop={id(current_loop)}"
            )
        self._started = False
        self._startup_loop = None

    @property
    def turn_setup(self) -> AgentTurnSetup:
        return AgentTurnSetup()

    async def run_chat_turn(self, task: GtAgentTask, synced_count: int) -> None:
        raise NotImplementedError
