import json
import os


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "../../config/agents_v3.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_api_key() -> str:
    config_path = os.path.join(os.path.dirname(__file__), "../../config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)["anthropic"]["api_key"]
