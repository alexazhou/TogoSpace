from typing import Any, List, Optional
import os

from pydantic import BaseModel, ConfigDict, Field
from constants import LlmServiceType, DriverType
import appPaths

# 多语言字段类型
I18nText = dict[str, str]   # e.g. {"zh-CN": "研究员", "en": "Researcher"}
I18nData = dict[str, I18nText]  # e.g. {"display_name": {"zh-CN": "研究员", "en": "Researcher"}}


class DeptNodeConfig(BaseModel):
    """递归的部门树节点，对应 config 中 dept_tree 的每个节点（配置文件用）。"""
    dept_name: str = ""
    i18n: "I18nData | None" = None  # 含 dept_name, responsibility 等多语言字段
    responsibility: str = ""
    manager: str
    agents: List[str] = Field(default_factory=list)
    children: List["DeptNodeConfig"] = Field(default_factory=list)


DeptNodeConfig.model_rebuild()


def _default_workspace_root() -> str:
    return appPaths.WORKSPACE_ROOT


def _is_test_env() -> bool:
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def _default_persistence_db_path() -> str:
    env_override = os.environ.get("TEAMAGENT_DB_PATH")
    if env_override and env_override.strip():
        return env_override.strip()
    if _is_test_env():
        return "../test_data/data.db"  # 相对路径，由 db.resolve_db_path 解析为 repo/test_data/
    return os.path.join(appPaths.DATA_DIR, "data.db")


def _default_llm_extra_headers() -> dict[str, str]:
    return {"User-Agent": "openclaw"}


class AgentConfig(BaseModel):
    """Configuration for an agent in a team, referencing a role template."""
    name: str  # Nickname of the agent in the team
    i18n: I18nData | None = None  # 多语言数据，含 display_name
    role_template: str  # Name of the RoleTemplate to use in config import/export
    model: Optional[str] = None  # 覆盖 RoleTemplate.model
    driver: DriverType = DriverType.TSP


class TeamRoomConfig(BaseModel):
    """Single room item in team config."""
    id: Optional[int] = None
    name: str = ""
    i18n: I18nData | None = None  # 含 display_name, initial_topic 等多语言字段
    agents: List[str]
    initial_topic: str = ""  # 保留旧格式兼容
    max_turns: int | None = None
    biz_id: str | None = None
    tags: List[str] = Field(default_factory=list)


class TeamConfig(BaseModel):
    """Canonical team config shape loaded from JSON/DB."""
    uuid: str | None = None  # 团队唯一标识，用于 UUID 去重
    name: str
    i18n: I18nData | None = None  # 多语言数据，含 display_name
    config: dict[str, Any] = Field(default_factory=dict)
    agents: List[AgentConfig] = Field(default_factory=list)
    dept_tree: Optional[DeptNodeConfig] = None
    preset_rooms: List[TeamRoomConfig] = Field(default_factory=list)
    auto_start: bool = True  # 导入后是否自动启动（enabled）；False 则以停用状态导入


class RoleTemplateConfig(BaseModel):
    """Role template definition loaded from config/role_templates/*.json."""
    name: str
    i18n: I18nData | None = None  # 多语言数据，含 display_name
    soul: str = ""
    prompt_file: str = ""
    model: Optional[str] = None
    allowed_tools: List[str] | None = None


class LlmServiceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    base_url: str
    api_key: str
    type: LlmServiceType
    model: str = "qwen-plus"
    enable: bool = True
    extra_headers: dict[str, str] = Field(default_factory=_default_llm_extra_headers)

    temperature: Optional[float] = None
    reasoning_effort: Optional[str] = None  # 推理强度，如 "high"；传入 litellm 后自动触发 Responses API

    # Token 预算与自动压缩配置
    context_window_tokens: int = 131072
    reserve_output_tokens: int = 8192
    compact_trigger_ratio: float = Field(default=0.85, ge=0.0, le=1.0)
    compact_summary_max_tokens: int = 6 * 1024


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

    language: str = "zh-CN"  # 界面语言，默认中文
    default_llm_server: str | None = None
    llm_services: list[LlmServiceConfig] = Field(default_factory=list)
    default_room_max_turns: int = 100
    persistence: PersistenceConfig | None = Field(default_factory=PersistenceConfig)
    workspace_root: str | None = Field(default_factory=_default_workspace_root)
    bind_host: str = "0.0.0.0"  # HTTP 服务绑定地址
    bind_port: int = 8080       # HTTP 服务绑定端口

    def model_post_init(self, __context: Any) -> None:
        if self.persistence is None:
            raise ValueError("persistence 不允许为 null")
        if self.workspace_root is None:
            raise ValueError("workspace_root 不允许为 null")

    @property
    def is_llm_configured(self) -> bool:
        """是否已配置可用的 LLM 服务（至少一个已启用）。"""
        return any(s.enable for s in self.llm_services)

    @property
    def current_llm_service(self) -> LlmServiceConfig | None:
        enabled_services = [s for s in self.llm_services if s.enable]
        if not enabled_services:
            return None

        if self.default_llm_server:
            for service in enabled_services:
                if service.name == self.default_llm_server:
                    return service

        return enabled_services[0]

    def get_default_team_workdir(self, team_name: str) -> str:
        return os.path.join(self.workspace_root, team_name)


class AppConfig(BaseModel):
    setting: SettingConfig = Field(default_factory=SettingConfig)
    role_templates: List[RoleTemplateConfig] = Field(default_factory=list)
    teams: List[TeamConfig] = Field(default_factory=list)
