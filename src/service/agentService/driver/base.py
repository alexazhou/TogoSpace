from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from constants import AgentHistoryTag, DriverType
from service.agentService.agentHistroy import AgentHistory
from service.roomService import ChatRoom
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


class AgentDriverHost(Protocol):
    name: str
    team_name: str
    system_prompt: str
    model: str
    team_workdir: str
    workspace_root: str
    current_room: ChatRoom
    _history: AgentHistory

    @property
    def key(self) -> str:
        ...

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        ...

    async def _execute_tool(self) -> None:
        ...

    async def append_history_message(
        self,
        message: llmApiUtil.OpenAIMessage,
        tags: list[AgentHistoryTag] | None = None,
    ) -> None:
        ...


class AgentDriver:
    def __init__(self, host: AgentDriverHost, config: AgentDriverConfig):
        self.host = host
        self.config = config

    @property
    def driver_type(self) -> DriverType:
        return self.config.driver_type

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        raise NotImplementedError
