from typing import Any, List, Optional
import os

from pydantic import BaseModel, ConfigDict, Field
from constants import LlmServiceType, DriverType


class DeptNodeConfig(BaseModel):
    """递归的部门树节点，对应 config 中 dept_tree 的每个节点。"""
    dept_name: str
    dept_responsibility: str = ""
    manager: str
    members: List[str] = Field(default_factory=list)
    children: List["DeptNodeConfig"] = Field(default_factory=list)


DeptNodeConfig.model_rebuild()


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


class AgentConfig(BaseModel):
    """Configuration for an agent in a team, referencing a role template."""
    name: str  # Nickname of the agent in the team
    role_template: str  # Name of the RoleTemplate to use in config import/export
    model: Optional[str] = None  # 覆盖 RoleTemplate.model
    driver: DriverType = DriverType.NATIVE  # 覆盖 RoleTemplate.driver


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
    config: dict[str, Any] = Field(default_factory=dict)
    members: List[AgentConfig] = Field(default_factory=list)
    dept_tree: Optional[DeptNodeConfig] = None
    preset_rooms: List[TeamRoomConfig] = Field(default_factory=list)
    max_function_calls: Optional[int] = None


class RoleTemplateConfig(BaseModel):
    """Role template definition loaded from config/role_templates/*.json."""
    name: str
    soul: str = ""
    prompt_file: str = ""
    model: Optional[str] = None
    allowed_tools: List[str] | None = None
    driver: DriverType | None = None


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
    setting: SettingConfig = Field(default_factory=SettingConfig)
    role_templates: List[RoleTemplateConfig] = Field(default_factory=list)
    teams: List[TeamConfig] = Field(default_factory=list)
    group_chat_prompt: str = ""
