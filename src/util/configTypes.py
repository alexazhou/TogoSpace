from typing import Any, List, Optional
import os

from pydantic import BaseModel, ConfigDict, Field
from constants import LlmServiceType


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
    allowed_tools: List[str] = Field(default_factory=list)
    driver: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)


class LlmServiceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    base_url: str
    api_key: str
    type: LlmServiceType
    model: str = "qwen-plus"
    enable: bool = True


class PersistenceConfig(BaseModel):
    enabled: bool = False
    db_path: str = Field(default_factory=_default_persistence_db_path)

    def model_post_init(self, __context: Any) -> None:
        value = self.db_path
        if value is None:
            self.db_path = _default_persistence_db_path()
            return
        if isinstance(value, str):
            stripped = value.strip()
            self.db_path = stripped or _default_persistence_db_path()
            return
        raise ValueError("persistence.db_path 必须为字符串")


class SettingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default_llm_server: str | None = None
    llm_services: list[LlmServiceConfig] = Field(default_factory=list)
    persistence: PersistenceConfig | None = Field(default_factory=PersistenceConfig)
    workspace_root: str | None = Field(default_factory=_default_workspace_root)

    def model_post_init(self, __context: Any) -> None:
        if self.persistence is None:
            raise ValueError("persistence 不允许为 null")
        if self.workspace_root is None:
            raise ValueError("workspace_root 不允许为 null")
        _ = self.current_llm_service

    @property
    def current_llm_service(self) -> LlmServiceConfig:
        enabled_services = [s for s in self.llm_services if s.enable]
        if not enabled_services:
            raise ValueError("未配置可用的 LLM 服务（llm_services 全部被禁用或为空）")

        active_key = self.default_llm_server or enabled_services[0].name
        services = {s.name: s for s in enabled_services}

        if active_key not in services:
            raise ValueError(f"默认 LLM 服务 '{active_key}' 未在 llm_services 中定义或已禁用")

        return services[active_key]

    def get_default_team_workdir(self, team_name: str) -> str:
        return os.path.join(self.workspace_root, team_name)

class AppConfig(BaseModel):
    agents: List[AgentConfig]
    teams: List[TeamConfig]
    setting: SettingConfig


__all__ = ["TeamMemberConfig", "TeamRoomConfig", "TeamConfig", "AgentConfig",
           "LlmServiceType", "LlmServiceConfig", "PersistenceConfig", "AppConfig", "SettingConfig"]
