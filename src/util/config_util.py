import json
import os


def load_config(path: str = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "../../config/agents_v3.2.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
