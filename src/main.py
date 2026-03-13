import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

import tornado.httpserver
import util.llm_api_util as llm_api_util
from util.config_util import load_agents, load_teams, load_llm_service_config
from service import message_bus, scheduler_service as scheduler, agent_service, room_service as chat_room, llm_service, func_tool_service
from route import make_app


def _setup_logger() -> None:
    log_dir = os.path.join(os.getcwd(), "../logs/backend")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v3_chat_{timestamp}.log")

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


_RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../run")
_PID_FILE = os.path.join(_RUN_DIR, "backend.pid")


def _check_single_instance() -> None:
    os.makedirs(_RUN_DIR, exist_ok=True)
    # 读取已有 PID，检查进程是否存活
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # 进程存活则抛 OSError
        print(f"后端已在运行（PID {pid}），拒绝启动第二个实例。", file=sys.stderr)
        sys.exit(1)
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass  # 文件不存在、内容非法、进程不存在，均视为可启动


def _write_pid() -> None:
    os.makedirs(_RUN_DIR, exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid() -> None:
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


async def main(config_dir: str = None, llm_config_path: str = None, port: int = 8080):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    _setup_logger()
    logger = logging.getLogger(__name__)

    agents_config = load_agents(config_dir)
    teams_config = load_teams(config_dir)
    llm_cfg = load_llm_service_config(llm_config_path)

    message_bus.init()
    llm_api_util.init()
    llm_service.init(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])
    func_tool_service.init()

    agent_service.init()
    agent_service.load_agent_config(agents_config)
    agent_service.create_team_agents(teams_config)

    chat_room.init()
    chat_room.create_rooms(teams_config)

    scheduler.init(teams_config=teams_config)

    web_server = tornado.httpserver.HTTPServer(make_app())
    web_server.listen(port, "0.0.0.0")

    n_teams = len(teams_config)
    n_rooms = len(chat_room.get_all_rooms())
    n_agents = len(agent_service.get_all_agents())

    async def _log_ready():
        await asyncio.sleep(0)
        logger.info(
            f"后端启动完成 — 监听 http://0.0.0.0:{port}，"
            f"共加载 {n_teams} 个 team、{n_rooms} 个聊天室、{n_agents} 个 agent 实例"
        )

    try:
        await asyncio.gather(
            scheduler.run(),
            asyncio.Event().wait(),
            _log_ready(),
        )
    finally:
        web_server.stop()
        scheduler.stop()
        agent_service.close()
        func_tool_service.close()
        chat_room.close_all()
        message_bus.stop()
        _remove_pid()


if __name__ == "__main__":
    _check_single_instance()
    _write_pid()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=None, dest="config_dir", help="config 目录路径")
    parser.add_argument("--llm-config", default=None, dest="llm_config", help="LLM 服务配置文件路径")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 监听端口")
    args = parser.parse_args()
    asyncio.run(main(config_dir=args.config_dir, llm_config_path=args.llm_config, port=args.port))
