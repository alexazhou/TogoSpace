import glob
import json
import os
from typing import List


def _default_config_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "../../config")


def _default_root_config_path() -> str:
    return os.path.join(os.path.dirname(__file__), "../../config.json")


def _resolve_config_file(config_dir: str | None, preferred_name: str) -> str:
    if config_dir is None:
        return _default_root_config_path()

    preferred_path = os.path.join(config_dir, preferred_name)
    if os.path.isfile(preferred_path):
        return preferred_path

    fallback_path = os.path.join(config_dir, "config.json")
    if os.path.isfile(fallback_path):
        return fallback_path

    llm_path = os.path.join(config_dir, "llm.json")
    if os.path.isfile(llm_path):
        return llm_path

    return preferred_path


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
    path = _resolve_config_file(config_dir, "llm.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 支持两种格式：
    # 1. llm.json 格式: {"active_llmService": "...", "llmServices": [...]}
    # 2. config.json 格式: {"LlmServices": [...], "active_LlmService": "..."}
    active_key = cfg.get("active_llmService") or cfg.get("active_LlmService")
    services_key = cfg.get("llmServices") or cfg.get("LlmServices")

    if not active_key:
        active_key = list(services_key.keys())[0] if services_key else None

    services = {s["name"]: s for s in services_key} if services_key else {}
    if active_key not in services:
        raise ValueError(f"active_LlmService '{active_key}' 未在 LlmServices 中定义")
    return dict(services[active_key])


def load_persistence_config(config_dir: str = None) -> dict:
    """返回持久化配置，未配置时提供默认值。"""
    path = _resolve_config_file(config_dir, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    persistence = cfg.get("persistence", {})
    return {
        "enabled": persistence.get("enabled", False),
        "db_path": persistence.get("db_path", "./runtime/state/teamagent.db"),
    }
