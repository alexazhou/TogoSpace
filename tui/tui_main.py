import argparse
import json
import logging
import os
import signal
import sys
from datetime import datetime

_TUI_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_TUI_DIR, "../logs/tui")
_RUN_DIR = os.path.join(_TUI_DIR, "../run")
_PID_FILE = os.path.join(_RUN_DIR, "tui.pid")

from app import WatcherApp


def _check_single_instance() -> None:
    os.makedirs(_RUN_DIR, exist_ok=True)
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        print(f"TUI 已在运行（PID {pid}），拒绝启动第二个实例。", file=sys.stderr)
        sys.exit(1)
    except (FileNotFoundError, ValueError, ProcessLookupError):
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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = os.path.join(_LOG_DIR, f"tui_{timestamp}.log")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger("tui").setLevel(logging.DEBUG)
    logging.getLogger("tui").addHandler(handler)

_DEFAULT_CONFIG = os.path.expanduser("~/.agent_team/setting.json")


def _load_base_url(config_path: str) -> str:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        srv = cfg.get("web_server") or cfg.get("server", {})
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
        help="后端地址，默认从 setting.json 读取",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        help="setting.json 路径",
    )
    args = parser.parse_args()

    _check_single_instance()
    _write_pid()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    _setup_logging()
    try:
        base_url = args.base_url or _load_base_url(args.config)
        app = WatcherApp(base_url=base_url)
        app.run()
    finally:
        _remove_pid()


if __name__ == "__main__":
    main()
