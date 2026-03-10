import argparse
import json
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

# Fix working directory to tui/ and allow imports from it
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from app import WatcherApp

_LOG_DIR = os.path.join(os.path.dirname(__file__), "../logs/tui")
_RUN_DIR = os.path.join(os.path.dirname(__file__), "../run")
_PID_FILE = os.path.join(_RUN_DIR, "tui.pid")


def _check_single_instance() -> None:
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        print(f"TUI 已在运行（PID {pid}），拒绝启动第二个实例。", file=sys.stderr)
        sys.exit(1)
    except (FileNotFoundError, ValueError):
        pass
    except OSError:
        pass


def _write_pid() -> None:
    os.makedirs(_RUN_DIR, exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid() -> None:
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


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

    _check_single_instance()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    _setup_logging()
    _write_pid()
    try:
        base_url = args.base_url or _load_base_url(args.config)
        app = WatcherApp(base_url=base_url)
        app.run()
    finally:
        _remove_pid()


if __name__ == "__main__":
    main()
