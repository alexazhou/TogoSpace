import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Allow imports from tui/ directory without package install
sys.path.insert(0, os.path.dirname(__file__))

from app import WatcherApp

_LOG_DIR = os.path.join(os.path.dirname(__file__), "../logs/tui")


def _setup_logging() -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    log_path = os.path.join(_LOG_DIR, "tui.log")
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger("tui").setLevel(logging.DEBUG)
    logging.getLogger("tui").addHandler(handler)

_DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "../config.json")


def _load_base_url(config_path: str) -> str:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        srv = cfg.get("server", {})
        host = srv.get("host", "127.0.0.1")
        port = srv.get("port", 8080)
        return f"http://{host}:{port}"
    except (FileNotFoundError, KeyError, ValueError):
        return "http://127.0.0.1:8080"


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 聊天室终端观察台")
    parser.add_argument(
        "--base-url",
        default=None,
        dest="base_url",
        help="后端地址，默认从 config.json 读取",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        help="config.json 路径",
    )
    args = parser.parse_args()

    _setup_logging()
    base_url = args.base_url or _load_base_url(args.config)
    app = WatcherApp(base_url=base_url)
    app.run()


if __name__ == "__main__":
    main()
