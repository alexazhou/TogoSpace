from dataclasses import dataclass, field
from typing import Any, List

from typing_extensions import NotRequired, Required, TypedDict

class TeamMemberConfig(TypedDict):
    name: Required[str]
    agent: Required[str]


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
    members: Required[list[TeamMemberConfig]]
    preset_rooms: Required[list[TeamRoomConfig]]
    max_function_calls: NotRequired[int]


class TeamConfigPatch(TypedDict, total=False):
    """Update payload shape for partial team updates."""

    name: Required[str]
    members: list[TeamMemberConfig]
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


def normalize_team_members(raw_members: list[Any]) -> list[TeamMemberConfig]:
    return [
        {
            "name": str(member["name"]),
            "agent": str(member["agent"]),
        }
        for member in raw_members
    ]


def normalize_team_config(team_config: dict[str, Any]) -> TeamConfig:
    rooms = [
        {
            **room,
            "members": [str(member) for member in room.get("members", [])],
        }
        for room in team_config.get("preset_rooms", [])
    ]

    normalized: TeamConfig = {
        "name": str(team_config["name"]),
        "members": normalize_team_members(team_config["members"]),
        "preset_rooms": rooms,
    }

    if "max_function_calls" in team_config:
        normalized["max_function_calls"] = int(team_config["max_function_calls"])

    return normalized


def get_team_member_map(team_config: TeamConfig) -> dict[str, TeamMemberConfig]:
    return {member["name"]: member for member in team_config.get("members", [])}


__all__ = ["TeamMemberConfig", "TeamRoomConfig", "TeamConfig", "TeamConfigPatch", "AgentConfig",
           "normalize_team_members", "normalize_team_config", "get_team_member_map",
           "LlmServiceConfig", "PersistenceConfig", "AppConfig"]
