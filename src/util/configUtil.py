import glob
import json
import os
from typing import Any, List

from util.configTypes import AgentConfig, AppConfig, LlmServiceConfig, PersistenceConfig, SettingConfig, TeamConfig


def _default_config_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "../../config")


def _default_root_config_path() -> str:
    return os.path.join(os.path.dirname(__file__), "../../config/setting.json")


def _default_workspace_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def _is_test_env() -> bool:
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def _default_db_path() -> str:
    return "../test_data/data.db" if _is_test_env() else "../data/data.db"


def _resolve_config_file(config_dir: str | None, preferred_name: str) -> str:
    if config_dir is None:
        return _default_root_config_path()
    return os.path.join(config_dir, preferred_name)


def load_json_objects_from_dir(dir_path: str) -> list[dict[str, Any]]:
    """加载目录下全部 json 文件，按文件名排序返回 json 对象列表。"""
    result: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(dir_path, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"JSON 文件内容必须是对象: {path}")
        result.append(data)
    return result


def load_agents(config_dir: str = None) -> List[AgentConfig]:
    """扫描 config/agents/*.json，返回 Agent 定义列表。"""
    if config_dir is None:
        config_dir = _default_config_dir()
    agents_dir = os.path.join(config_dir, "agents")
    raw_agents = load_json_objects_from_dir(agents_dir)
    return [AgentConfig.model_validate(agent) for agent in raw_agents]


def load_teams(config_dir: str = None) -> List[TeamConfig]:
    """扫描 config/teams/*.json，返回 Team 定义列表。"""
    if config_dir is None:
        config_dir = _default_config_dir()
    teams_dir = os.path.join(config_dir, "teams")
    raw_teams = load_json_objects_from_dir(teams_dir)
    return [TeamConfig.model_validate(team) for team in raw_teams]


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_setting_config(config_dir: str = None) -> SettingConfig:
    """加载 setting.json 并转为 SettingConfig。文件不存在时返回默认对象。"""
    path = _resolve_config_file(config_dir, "setting.json")
    if not os.path.isfile(path):
        return SettingConfig()

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    return SettingConfig(
        default_llm_server=cfg.get("default_llm_server"),
        llm_services=list(cfg.get("llm_services") or []),
        persistence=dict(cfg.get("persistence") or {}),
        workspace_root=str(cfg.get("workspace_root")) if cfg.get("workspace_root") else None,
    )


def load_llmService_config(config_dir: str = None, setting: SettingConfig | None = None) -> LlmServiceConfig:
    """返回当前激活的 LLM 服务配置。"""
    setting = setting or load_setting_config(config_dir)

    active_key = setting.default_llm_server
    all_services = setting.llm_services
    enabled_services = [s for s in all_services if s.get("enable", True)]

    if not enabled_services:
        raise ValueError("未配置可用的 LLM 服务（llm_services 全部被禁用或为空）")

    if not active_key:
        active_key = enabled_services[0].get("name")

    services = {s["name"]: s for s in enabled_services if s.get("name")}
    if active_key not in services:
        raise ValueError(f"默认 LLM 服务 '{active_key}' 未在 llm_services 中定义或已禁用")
    selected = services[active_key]
    return LlmServiceConfig(
        name=str(selected["name"]),
        base_url=str(selected["base_url"]),
        api_key=str(selected["api_key"]),
        type=str(selected["type"]),
        model=str(selected["model"]) if selected.get("model") is not None else None,
        enable=bool(selected.get("enable", True)),
    )


def load_persistence_config(config_dir: str = None, setting: SettingConfig | None = None) -> dict:
    """返回持久化配置。"""
    setting = setting or load_setting_config(config_dir)
    default_db_path = _default_db_path()
    persistence = setting.persistence
    return {
        "enabled": persistence.get("enabled", False),
        "db_path": persistence.get("db_path", default_db_path),
    }


def load_workspace_root(config_dir: str = None, setting: SettingConfig | None = None) -> str:
    """返回工作区根目录。"""
    setting = setting or load_setting_config(config_dir)
    workspace_root = setting.workspace_root
    if workspace_root:
        return workspace_root
    return _default_workspace_root()


def load(config_dir: str = None) -> AppConfig:
    """一次性加载所有配置，返回有类型的 AppConfig 对象。"""
    agents = load_agents(config_dir)
    teams = load_teams(config_dir)

    setting = load_setting_config(config_dir)
    llm_service = load_llmService_config(config_dir, setting)
    persistence_dict = load_persistence_config(config_dir, setting)
    persistence = PersistenceConfig(**persistence_dict)
    workspace_root = load_workspace_root(config_dir, setting)

    return AppConfig(
        agents=agents,
        teams=teams,
        llm_service=llm_service,
        persistence=persistence,
        workspace_root=workspace_root,
    )
