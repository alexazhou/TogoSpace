from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from service.room_service import ChatRoom


@dataclass
class AgentDriverConfig:
    driver_type: str
    options: dict[str, Any] = field(default_factory=dict)


class AgentDriverHost(Protocol):
    name: str
    team_name: str
    system_prompt: str
    model: str
    current_room: Any
    _history: list[Any]
    _turn_ctx: Any

    @property
    def key(self) -> str:
        ...

    async def sync_room_messages(self, room: Any) -> int:
        ...

    async def _infer(self, tools=None):
        ...

    async def _execute_tool(self, tool_call_id: str, name: str, args: str) -> None:
        ...

    async def append_history_message(self, message: Any) -> None:
        ...

    async def send_chat_message(self, room_name: str, msg: str):
        ...

    def skip_chat_turn(self):
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
