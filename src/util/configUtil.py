import glob
import json
import os
from typing import Any, List

from util.configTypes import (
    AgentConfig,
    AppConfig,
    PersistenceConfig,
    SettingConfig,
    TeamConfig,
)

_cached_app_config: AppConfig | None = None
_cached_config_dir: str | None = None


def _resolve_config_dir(config_dir: str | None) -> str:
    base = config_dir or os.path.join(os.path.dirname(__file__), "../../config")
    return os.path.abspath(base)


def get_db_path() -> str:
    return PersistenceConfig().db_path


def _load_agents(config_dir: str) -> List[AgentConfig]:
    agents_dir = os.path.join(config_dir, "agents")
    raw_agents = load_json_objects_from_dir(agents_dir)
    return [AgentConfig.model_validate(agent) for agent in raw_agents]


def _load_teams(config_dir: str) -> List[TeamConfig]:
    teams_dir = os.path.join(config_dir, "teams")
    raw_teams = load_json_objects_from_dir(teams_dir)
    return [TeamConfig.model_validate(team) for team in raw_teams]


def _load_setting(config_dir: str) -> SettingConfig:
    path = os.path.join(config_dir, "setting.json")
    if not os.path.isfile(path):
        return SettingConfig()

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if not isinstance(cfg, dict):
        raise ValueError(f"setting.json 内容必须是对象: {path}")
    return SettingConfig.model_validate(cfg)


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


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_app_config() -> AppConfig:
    if _cached_app_config is None:
        raise RuntimeError("AppConfig 未初始化，请先调用 configUtil.load(...)")
    return _cached_app_config


def load(config_dir: str = None, force_reload: bool = False) -> AppConfig:
    """一次性加载所有配置，写入缓存并返回。"""
    global _cached_app_config, _cached_config_dir

    resolved_config_dir = _resolve_config_dir(config_dir)
    if not force_reload and _cached_app_config is not None and _cached_config_dir == resolved_config_dir:
        return _cached_app_config

    agents = _load_agents(resolved_config_dir)
    teams = _load_teams(resolved_config_dir)
    setting = _load_setting(resolved_config_dir)

    app_config = AppConfig(
        agents=agents,
        teams=teams,
        setting=setting,
    )
    _cached_app_config = app_config
    _cached_config_dir = resolved_config_dir
    return app_config
