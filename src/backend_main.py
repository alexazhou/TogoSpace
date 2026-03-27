import argparse
import asyncio
import logging
import os
import signal
import sys

import tornado.httpserver

from util import llmApiUtil, configUtil, logUtil
from util.configTypes import AppConfig
from service import (
    messageBus,
    schedulerService,
    roleTemplateService,
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
    llmApiUtil.init()

    # ── 阶段 1：基础启动 ──────────────────────────────────────────────────────
    logger.info("[启动] 阶段 1/4：基础 service 启动")
    await messageBus.startup()
    await llmService.startup()
    await funcToolService.startup()
    await ormService.startup(app_config.setting.persistence.db_path)
    await persistenceService.startup()
    logger.info("[启动] 阶段 1/4 完成")

    # ── 阶段 2：导入配置 ──────────────────────────────────────────────────────
    logger.info("[启动] 阶段 2/4：导入 Team / Agent 配置")
    await teamService.startup()
    teams_config = teamService.get_teams()
    await roleTemplateService.startup()
    roleTemplateService.load_role_template_config()
    logger.info("[启动] 阶段 2/4 完成：teams=%s", [t.name for t in teams_config])

    # ── 阶段 3：运行时构建 ────────────────────────────────────────────────────
    logger.info("[启动] 阶段 3/4：构建运行时（成员 / 房间 / 调度器）")
    await agentService.load_team_ids(teams_config)
    await agentService.startup()
    await agentService.create_team_agents(teams_config, workspace_root=app_config.setting.workspace_root)
    await roomService.startup()
    await schedulerService.startup(teams_config=teams_config)
    await roomService.create_rooms(teams_config)
    logger.info("[启动] 阶段 3/4 完成")

    # ── 阶段 4：恢复状态 ──────────────────────────────────────────────────────
    logger.info("[启动] 阶段 4/4：恢复持久化状态")
    await agentService.restore_state()
    await roomService.restore_state()
    activated = roomService.exit_init_rooms()
    logger.info("[启动] 阶段 4/4 完成：激活房间数=%s", activated)

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
