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


def _get_config_dir(config_dir: str | None) -> str:
    if config_dir:
        return config_dir
    return os.path.join(os.path.dirname(__file__), "../../config")


def get_db_path() -> str:
    return PersistenceConfig().db_path


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
    config_dir = _get_config_dir(config_dir)
    agents_dir = os.path.join(config_dir, "agents")
    raw_agents = load_json_objects_from_dir(agents_dir)
    return [AgentConfig.model_validate(agent) for agent in raw_agents]


def load_teams(config_dir: str = None) -> List[TeamConfig]:
    """扫描 config/teams/*.json，返回 Team 定义列表。"""
    config_dir = _get_config_dir(config_dir)
    teams_dir = os.path.join(config_dir, "teams")
    raw_teams = load_json_objects_from_dir(teams_dir)
    return [TeamConfig.model_validate(team) for team in raw_teams]


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_setting_config(config_dir: str = None) -> SettingConfig:
    """加载 setting.json 并转为 SettingConfig。文件不存在时返回默认对象。"""
    path = os.path.join(_get_config_dir(config_dir), "setting.json")
    if not os.path.isfile(path):
        return SettingConfig()

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if not isinstance(cfg, dict):
        raise ValueError(f"setting.json 内容必须是对象: {path}")
    return SettingConfig.model_validate(cfg)


def load(config_dir: str = None) -> AppConfig:
    """一次性加载所有配置，返回有类型的 AppConfig 对象。"""
    agents = load_agents(config_dir)
    teams = load_teams(config_dir)

    setting = load_setting_config(config_dir)

    return AppConfig(
        agents=agents,
        teams=teams,
        setting=setting,
    )
