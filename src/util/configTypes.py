from dataclasses import dataclass
from typing import Any, List, Optional
import os

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _default_workspace_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def _is_test_env() -> bool:
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def _default_persistence_db_path() -> str:
    return "../test_data/data.db" if _is_test_env() else "../data/data.db"

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
    persistence: PersistenceConfig = Field(
        default_factory=lambda: PersistenceConfig(db_path=_default_persistence_db_path())
    )
    workspace_root: str = Field(default_factory=_default_workspace_root)

    @field_validator("persistence", mode="before")
    @classmethod
    def normalize_persistence(cls, value: Any) -> dict[str, Any] | Any:
        if value is None:
            return {"enabled": False, "db_path": _default_persistence_db_path()}
        if isinstance(value, dict):
            normalized = dict(value)
            db_path = normalized.get("db_path")
            if db_path is None:
                normalized["db_path"] = _default_persistence_db_path()
            elif isinstance(db_path, str):
                stripped = db_path.strip()
                normalized["db_path"] = stripped or _default_persistence_db_path()
            return normalized
        return value

    @field_validator("workspace_root", mode="before")
    @classmethod
    def normalize_workspace_root(cls, value: Any) -> str:
        if value is None:
            return _default_workspace_root()
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            return _default_workspace_root()
        return value


def resolve_team_workdir(team_name: str, working_directory: str | None, workspace_root: str) -> str:
    if working_directory:
        return working_directory
    return os.path.join(workspace_root, team_name)


def get_team_member_map(team_config: TeamConfig) -> dict[str, TeamMemberConfig]:
    return {member.name: member for member in team_config.members}


__all__ = ["TeamMemberConfig", "TeamRoomConfig", "TeamConfig", "AgentConfig",
           "resolve_team_workdir", "get_team_member_map",
           "LlmServiceConfig", "PersistenceConfig", "AppConfig", "SettingConfig"]
