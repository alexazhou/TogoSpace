import os
from typing import Any, List, Optional

import appPaths
from constants import DriverType, LlmProtocol, LlmProviderType
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

# 多语言字段类型
I18nText = dict[str, str]   # e.g. {"zh-CN": "研究员", "en": "Researcher"}
I18nData = dict[str, I18nText]  # e.g. {"display_name": {"zh-CN": "研究员", "en": "Researcher"}}


class DeptNodePreset(BaseModel):
    """递归的部门树节点，对应 config 中 dept_tree 的每个节点（配置文件用）。"""
    dept_name: str = ""
    i18n: "I18nData | None" = None  # 含 dept_name, responsibility 等多语言字段
    responsibility: str = ""
    manager: str
    agents: List[str] = Field(default_factory=list)
    children: List["DeptNodePreset"] = Field(default_factory=list)


DeptNodePreset.model_rebuild()


def _default_workspace_root() -> str:
    if _is_test_env():
        return os.path.abspath(os.path.join(appPaths._ROOT, "test_data", "workspace"))
    return appPaths.WORKSPACE_ROOT


def _is_test_env() -> bool:
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def _default_db_path() -> str:
    env_override = os.environ.get("TEAMAGENT_DB_PATH")
    if env_override and env_override.strip():
        return env_override.strip()
    if _is_test_env():
        return "../test_data/data.db"  # 相对路径，由 db.resolve_db_path 解析为 repo/test_data/
    return os.path.join(appPaths.DATA_DIR, "data.db")


def _default_llm_extra_headers() -> dict[str, str]:
    return {"User-Agent": "opencode"}


_LLM_PROVIDER_PARAM_RESERVED_KEYS = {
    "api_key",
    "base_url",
    "cache_control_injection_points",
    "custom_llm_provider",
    "extra_headers",
    "max_tokens",
    "messages",
    "model",
    "stream",
    "temperature",
    "tool_choice",
    "tools",
}


def _validate_llm_provider_params(value: dict[str, Any]) -> dict[str, Any]:
    reserved_keys = sorted(_LLM_PROVIDER_PARAM_RESERVED_KEYS.intersection(value.keys()))
    if reserved_keys:
        raise ValueError(
            "provider_params 包含保留字段，不能覆盖系统请求参数："
            + ", ".join(reserved_keys)
        )
    return value


class AgentPreset(BaseModel):
    """Configuration for an agent in a team, referencing a role template."""
    name: str  # Nickname of the agent in the team
    i18n: I18nData | None = None  # 多语言数据，含 display_name
    role_template: str  # Name of the RoleTemplate to use in config import/export
    model: Optional[str] = None  # 覆盖 RoleTemplate.model
    driver: DriverType = DriverType.TSP
    allow_tools: List[str] | None = None
    allow_skills: List[str] | None = None


class TeamRoomPreset(BaseModel):
    """Single room item in team config."""
    id: Optional[int] = None
    name: str = ""
    i18n: I18nData | None = None  # 含 display_name, initial_topic 等多语言字段
    agents: List[str]
    initial_topic: str = ""  # 保留旧格式兼容
    max_rounds: int | None = None
    biz_id: str | None = None
    tags: List[str] = Field(default_factory=list)


class TeamPreset(BaseModel):
    """Canonical team config shape loaded from JSON/DB."""
    uuid: str | None = None  # 团队唯一标识，用于 UUID 去重
    name: str
    i18n: I18nData | None = None  # 多语言数据，含 display_name
    config: dict[str, Any] = Field(default_factory=dict)
    agents: List[AgentPreset] = Field(default_factory=list)
    dept_tree: Optional[DeptNodePreset] = None
    preset_rooms: List[TeamRoomPreset] = Field(default_factory=list)
    auto_start: bool = True  # 导入后是否自动启动（enabled）；False 则以停用状态导入
    is_default: bool = False  # 是否为默认团队（首次访问时优先展示）


class RoleTemplatePreset(BaseModel):
    """Role template definition loaded from config/role_templates/*.json."""
    name: str
    i18n: I18nData | None = None  # 多语言数据，含 display_name
    soul: str = ""
    prompt_file: str = ""


class LlmContextConfig(BaseModel):
    """上下文与压缩策略配置"""
    context_window_tokens: int = 131072
    reserve_output_tokens: int = 16384
    compact_trigger_ratio: float = Field(default=0.85, ge=0.0, le=1.0)
    compact_summary_max_tokens: int = 6144


class LlmModelConfig(BaseModel):
    """单个模型的配置 — 归属于某个提供商。"""
    name: str
    protocol: LlmProtocol
    enabled: bool = True
    support_vision: bool = False
    temperature: Optional[float] = None
    provider_params: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    context_config: Optional[LlmContextConfig] = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        cc = data.get("context_config")
        if cc is None or cc == {}:
            data.pop("context_config", None)
        return data

    @field_validator("provider_params")
    @classmethod
    def validate_provider_params(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        return _validate_llm_provider_params(value)


class LlmProviderConfig(BaseModel):
    """LLM 提供商配置 — 对应一个 API 服务提供商。"""
    name: str
    type: LlmProviderType
    api_key: str
    enable: bool = True
    urls: dict[str, str] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=_default_llm_extra_headers)
    provider_params: dict[str, Any] = Field(default_factory=dict)
    models: List[LlmModelConfig] = Field(default_factory=list)

    @field_validator("provider_params")
    @classmethod
    def validate_provider_params(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        return _validate_llm_provider_params(value)


class DefaultModelSlots(BaseModel):
    """全局默认模型槽位。"""
    primary: str = ""
    lightweight: str = ""
    vision: str = ""
    advanced: str = ""


class DemoModeConfig(BaseModel):
    enabled: bool = False
    freeze_data: bool = True
    hide_sensitive_info: bool = True

    @property
    def read_only(self) -> bool:
        return self.enabled and self.freeze_data

    @property
    def hide_sensitive(self) -> bool:
        return self.enabled and self.hide_sensitive_info


class AuthConfig(BaseModel):
    """鉴权配置。"""
    enabled: bool = False
    token: str = ""


class DevConfig(BaseModel):
    """开发配置，用于调试和测试。"""
    model_config = ConfigDict(extra="ignore")

    latest_release: str = ""  # 手动指定最新版本号，用于测试自动升级 UI；为空时走 GitHub API


class SettingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = "v2"
    language: str = "zh-CN"  # 界面语言，默认中文
    development_mode: bool = False  # 前端开发模式开关，影响错误提示等交互行为
    demo_mode: DemoModeConfig = Field(default_factory=DemoModeConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    llm_providers: List[LlmProviderConfig] = Field(default_factory=list)
    default_models: DefaultModelSlots = Field(default_factory=DefaultModelSlots)
    context_config: LlmContextConfig = Field(default_factory=LlmContextConfig)
    default_room_max_rounds: int = 100
    db_path: str = Field(default_factory=_default_db_path)
    workspace_root: str | None = Field(default_factory=_default_workspace_root)
    bind_host: str = "0.0.0.0"  # HTTP 服务绑定地址
    bind_port: int = 8180       # HTTP 服务绑定端口
    auto_check_update: bool = True  # 启动时自动检查更新
    dev: DevConfig = Field(default_factory=DevConfig)

    def model_post_init(self, __context: Any) -> None:
        if not self.db_path.strip():
            self.db_path = _default_db_path()
        if self.workspace_root is None:
            raise ValueError("workspace_root 不允许为 null")

    @property
    def is_llm_configured(self) -> bool:
        """是否已配置可用的 LLM 服务（至少有一个启用且有模型的提供商）。"""
        for provider in self.llm_providers:
            if provider.enable and any(m.enabled for m in provider.models):
                return True
        return False

    def get_default_team_workdir(self, team_name: str) -> str:
        return os.path.join(self.workspace_root, team_name)


class AppConfig(BaseModel):
    setting: SettingConfig = Field(default_factory=SettingConfig)
    role_templates_preset: List[RoleTemplatePreset] = Field(default_factory=list)
    teams_preset: List[TeamPreset] = Field(default_factory=list)
