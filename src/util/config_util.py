import json
import logging
import os
from datetime import datetime


def setup_logger() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v2_chat_{timestamp}.log")

    log_format = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    for handler in [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]:
        handler.setFormatter(logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S"))
        root_logger.addHandler(handler)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "../../config/agents_v2.json")
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
