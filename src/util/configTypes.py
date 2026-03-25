from dataclasses import dataclass
from typing import Any, List, Optional
import os

from pydantic import BaseModel, ConfigDict, Field

class TeamMemberConfig(BaseModel):
    name: str
    agent: str


class TeamRoomConfig(BaseModel):
    """Single room item in team config."""
    id: Optional[int] = None
    name: str
    members: List[str]
    initial_topic: str = ""
    max_turns: int = 10


class TeamConfig(BaseModel):
    """Canonical team config shape loaded from JSON/DB."""
    name: str
    working_directory: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    members: List[TeamMemberConfig] = Field(default_factory=list)
    preset_rooms: List[TeamRoomConfig] = Field(default_factory=list)
    max_function_calls: Optional[int] = None


class AgentConfig(BaseModel):
    """Agent definition loaded from config/agents/*.json."""
    name: str
    system_prompt: str = ""
    prompt_file: str = ""
    model: Optional[str] = None
    use_agent_sdk: bool = False
    allowed_tools: List[str] = Field(default_factory=list, alias="allowed_Tools")
    driver: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)

    class Config:
        populate_by_name = True


class LlmServiceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    workspace_root: str


class SettingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default_llm_server: str | None = None
    llm_services: list[dict[str, Any]] = Field(default_factory=list)
    persistence: dict[str, Any] = Field(default_factory=dict)
    workspace_root: str | None = None


def resolve_team_workdir(team_name: str, working_directory: str | None, workspace_root: str) -> str:
    if working_directory:
        return working_directory
    return os.path.join(workspace_root, team_name)


def get_team_member_map(team_config: TeamConfig) -> dict[str, TeamMemberConfig]:
    return {member.name: member for member in team_config.members}


__all__ = ["TeamMemberConfig", "TeamRoomConfig", "TeamConfig", "AgentConfig",
           "resolve_team_workdir", "get_team_member_map",
           "LlmServiceConfig", "PersistenceConfig", "AppConfig", "SettingConfig"]
