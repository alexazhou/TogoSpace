import argparse
import asyncio
import logging
import os
import signal
import sys

import tornado.httpserver

from util import llmApiUtil, configUtil, logUtil
from util.configTypes import TeamConfig, AppConfig
from service import (
    messageBus,
    schedulerService,
    agentService,
    roomService,
    llmService,
    funcToolService,
    persistenceService,
    ormService,
    teamService,
)
import route


def _setup_logger() -> None:
    logUtil.init_backend_logger()


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


async def main(config_dir: str = None, port: int = 8080):

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    _setup_logger()
    logger = logging.getLogger(__name__)

    if config_dir is not None:
        config_dir = os.path.abspath(config_dir)

    app_config: AppConfig = configUtil.load(config_dir)
    llm_config = app_config.setting.current_llm_service

    llmApiUtil.init()
    await messageBus.startup()
    await llmService.startup(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        model=llm_config.model,
    )
    await funcToolService.startup()

    await ormService.startup(app_config.setting.persistence.db_path)
    await persistenceService.startup()

    # 从 teamService 加载 Team 配置（会自动从 JSON 导入到数据库）
    teams_config: list[TeamConfig] = await teamService.startup(app_config.teams)

    # 加载 team_id 映射
    await agentService.load_team_ids(teams_config)

    await agentService.startup()
    agentService.load_agent_config(app_config.agents)
    await agentService.create_team_agents(teams_config, workspace_root=app_config.setting.workspace_root)

    await roomService.startup()
    await schedulerService.startup(teams_config=teams_config)
    await roomService.create_rooms(teams_config)
    await persistenceService.restore_runtime_state(agentService.get_all_agents(), roomService.get_all_rooms())
    activated = roomService.exit_init_rooms()
    logger.info("启动激活完成：退出 INIT. 房间数=%s", activated)

    web_server = tornado.httpserver.HTTPServer(route.application)
    web_server.listen(port, "0.0.0.0")

    try:
        schedulerService.replay_scheduling_rooms()
        await schedulerService.run()
    finally:
        web_server.stop()
        schedulerService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        funcToolService.shutdown()
        roomService.shutdown()
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
