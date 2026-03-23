import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from typing import List

import tornado.httpserver

from util import llmApiUtil, configUtil
from util.configTypes import TeamConfig
load_agents = configUtil.load_agents
load_llmService_config = configUtil.load_llmService_config
load_persistence_config = configUtil.load_persistence_config
from service import (
    messageBus,
    schedulerService as scheduler,
    agentService,
    roomService as chat_room,
    llmService,
    funcToolService,
    persistenceService,
    ormService,
    teamService,
)
import route


def _setup_logger() -> None:
    log_dir = os.path.join(os.getcwd(), "../logs/backend")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v3_chat_{timestamp}.log")

    log_format = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    handlers: List[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
    for handler in handlers:
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


def _load_runtime_configs(config_dir: str = None) -> tuple[dict, dict]:
    llm_cfg = load_llmService_config(config_dir)
    persistence_cfg = load_persistence_config(config_dir)
    return llm_cfg, persistence_cfg


async def main(
    config_dir: str = None,
    port: int = 8080,
):
    if config_dir is not None:
        config_dir = os.path.abspath(config_dir)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    _setup_logger()
    logger = logging.getLogger(__name__)

    agents_config = load_agents(config_dir)
    llm_cfg, persistence_cfg = _load_runtime_configs(config_dir)

    await messageBus.startup()
    llmApiUtil.init()
    await llmService.startup(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])
    await funcToolService.startup()

    await ormService.startup(persistence_cfg["db_path"])
    await persistenceService.startup()

    # 从 teamService 加载 Team 配置（会自动从 JSON 导入到数据库）
    teams_config: list[TeamConfig] = await teamService.startup(config_dir)

    # 加载 team_id 映射
    await agentService.load_team_ids(teams_config)

    await agentService.startup()
    agentService.load_agent_config(agents_config)
    await agentService.create_team_agents(teams_config)

    await chat_room.startup()
    await scheduler.startup(teams_config=teams_config)
    await chat_room.create_rooms(teams_config)
    await persistenceService.restore_runtime_state(agentService.get_all_agents(), chat_room.get_all_rooms())
    activated = chat_room.exit_init_rooms()
    logger.info("启动激活完成：退出 INIT 房间数=%s", activated)

    web_server = tornado.httpserver.HTTPServer(route.application)
    web_server.listen(port, "0.0.0.0")

    try:
        scheduler.replay_scheduling_rooms()
        await scheduler.run()
    finally:
        web_server.stop()
        scheduler.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        funcToolService.shutdown()
        chat_room.shutdown()
        llmService.shutdown()
        messageBus.shutdown()
        _remove_pid()


if __name__ == "__main__":
    _check_single_instance()
    _write_pid()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=None, dest="config_dir", help="config 目录路径")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 监听端口")
    args = parser.parse_args()
    asyncio.run(main(config_dir=args.config_dir, port=args.port))
