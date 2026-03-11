import glob
import json
import os
from typing import List


def load_agents(resource_dir: str = None) -> List[dict]:
    """扫描 resource/agents/*.json，返回 Agent 定义列表。"""
    if resource_dir is None:
        resource_dir = os.path.join(os.path.dirname(__file__), "../../resource")
    agents_dir = os.path.join(resource_dir, "agents")
    result = []
    for path in sorted(glob.glob(os.path.join(agents_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            result.append(json.load(f))
    return result


def load_teams(resource_dir: str = None) -> List[dict]:
    """扫描 resource/teams/*.json，返回 Team 定义列表。"""
    if resource_dir is None:
        resource_dir = os.path.join(os.path.dirname(__file__), "../../resource")
    teams_dir = os.path.join(resource_dir, "teams")
    result = []
    for path in sorted(glob.glob(os.path.join(teams_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            result.append(json.load(f))
    return result


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_llm_service_config(path: str = None) -> dict:
    """返回当前激活的 LLM 服务配置（name, base_url, api_key, type）。"""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "../../config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    active = cfg["active_llm_service"]
    services = {s["name"]: s for s in cfg["llm_services"]}
    if active not in services:
        raise ValueError(f"active_llm_service '{active}' 未在 llm_services 中定义")
    return services[active]
