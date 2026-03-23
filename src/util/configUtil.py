import glob
import json
import os
from typing import List


def _default_config_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "../../config")


def _default_root_config_path() -> str:
    return os.path.join(os.path.dirname(__file__), "../../config/setting.json")


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


def load_agents(config_dir: str = None) -> List[dict]:
    """扫描 config/agents/*.json，返回 Agent 定义列表。"""
    if config_dir is None:
        config_dir = _default_config_dir()
    agents_dir = os.path.join(config_dir, "agents")
    result = []
    for path in sorted(glob.glob(os.path.join(agents_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            result.append(json.load(f))
    return result


def load_teams(config_dir: str = None) -> List[dict]:
    """扫描 config/teams/*.json，返回 Team 定义列表。"""
    if config_dir is None:
        config_dir = _default_config_dir()
    teams_dir = os.path.join(config_dir, "teams")
    result = []
    for path in sorted(glob.glob(os.path.join(teams_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            result.append(json.load(f))
    return result


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_llmService_config(config_dir: str = None) -> dict:
    """返回当前激活的 LLM 服务配置（name, base_url, api_key, type）。"""
    path = _resolve_config_file(config_dir, "setting.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    active_key = cfg.get("default_llm_server")
    services_key = cfg.get("llm_services")

    all_services = services_key or []
    enabled_services = [s for s in all_services if s.get("enable", True)]

    if not enabled_services:
        raise ValueError("未配置可用的 LLM 服务（llm_services 全部被禁用或为空）")

    if not active_key:
        active_key = enabled_services[0].get("name")

    services = {s["name"]: s for s in enabled_services if s.get("name")}
    if active_key not in services:
        raise ValueError(f"默认 LLM 服务 '{active_key}' 未在 llm_services 中定义或已禁用")
    return dict(services[active_key])


def load_persistence_config(config_dir: str = None) -> dict:
    """返回持久化配置。"""
    path = _resolve_config_file(config_dir, "setting.json")
    default_db_path = _default_db_path()
    if not os.path.isfile(path):
        return {
            "enabled": False,
            "db_path": default_db_path,
        }

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    persistence = cfg.get("persistence", {})
    return {
        "enabled": persistence.get("enabled", False),
        "db_path": persistence.get("db_path", default_db_path),
    }
