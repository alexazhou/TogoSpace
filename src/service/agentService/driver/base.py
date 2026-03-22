from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from service.roomService import ChatRoom
from util import llm_api_util


@dataclass
class AgentDriverConfig:
    driver_type: str
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
    current_room: ChatRoom
    _history: list[llm_api_util.LlmApiMessage]

    @property
    def key(self) -> str:
        ...

    async def _infer(self, tools: Optional[list[llm_api_util.Tool]]) -> llm_api_util.LlmApiMessage:
        ...

    async def _execute_tool(self) -> None:
        ...

    def get_last_assistant_message(self, start_idx: int = 0) -> Optional[llm_api_util.LlmApiMessage]:
        ...

    async def append_history_message(self, message: llm_api_util.LlmApiMessage) -> None:
        ...


class AgentDriver:
    def __init__(self, host: AgentDriverHost, config: AgentDriverConfig):
        self.host = host
        self.config = config

    @property
    def driver_type(self) -> str:
        return self.config.driver_type

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        raise NotImplementedError
