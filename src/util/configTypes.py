from dataclasses import dataclass, field
from typing import Any, List

from typing_extensions import NotRequired, Required, TypedDict


class TeamRoomConfig(TypedDict, total=False):
    """Single room item in team config."""

    id: int
    name: Required[str]
    members: Required[list[str]]
    initial_topic: str
    max_turns: int


class TeamConfig(TypedDict, total=False):
    """Canonical team config shape loaded from JSON/DB."""

    name: Required[str]
    members: Required[list[str]]
    preset_rooms: Required[list[TeamRoomConfig]]
    max_function_calls: NotRequired[int]


class TeamConfigPatch(TypedDict, total=False):
    """Update payload shape for partial team updates."""

    name: Required[str]
    members: list[str]
    preset_rooms: list[TeamRoomConfig]
    max_function_calls: int


class AgentConfig(TypedDict, total=False):
    """Agent definition loaded from config/agents/*.json."""

    name: Required[str]
    system_prompt: str
    prompt_file: str
    model: NotRequired[str]
    use_agent_sdk: bool
    allowed_tools: list[str]
    allowed_Tools: list[str]
    driver: dict[str, Any]
    runtime: dict[str, Any]


@dataclass
class LlmServiceConfig:
    name: str
    base_url: str
    api_key: str
    type: str
    model: str | None = None
    enable: bool = True


@dataclass
class PersistenceConfig:
    enabled: bool = False
    db_path: str = "../data/data.db"


@dataclass
class AppConfig:
    agents: List[AgentConfig]
    teams: List[TeamConfig]
    llm_service: LlmServiceConfig
    persistence: PersistenceConfig


__all__ = ["TeamRoomConfig", "TeamConfig", "TeamConfigPatch", "AgentConfig",
           "LlmServiceConfig", "PersistenceConfig", "AppConfig"]
