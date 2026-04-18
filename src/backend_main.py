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
    agentService,
    roomService,
    teamService,
    llmService,
    funcToolService,
    persistenceService,
    ormService,
    presetService,
)
import appPaths
import route
from version import __version__
from dal.db import gtTeamManager


def _setup_logger() -> None:
    logUtil.init_backend_logger()


_RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../run")
_PID_FILE = os.path.join(_RUN_DIR, "backend.pid")
_shutdown_event: asyncio.Event | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def _check_single_instance() -> None:
    # 测试环境允许多实例并行运行（不同端口）
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return
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
    # 测试环境不写 PID 文件
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return
    os.makedirs(_RUN_DIR, exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid() -> None:
    # 测试环境不处理 PID 文件
    if os.environ.get("TEAMAGENT_ENV") == "test":
        return
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


def request_shutdown() -> None:
    """请求后端主循环退出。"""
    loop = _main_loop
    shutdown_event = _shutdown_event
    if loop is None or shutdown_event is None:
        return
    if loop.is_running():
        loop.call_soon_threadsafe(shutdown_event.set)
    else:
        shutdown_event.set()


def _handle_shutdown_signal(signum: int, _frame) -> None:
    logger = logging.getLogger(__name__)
    try:
        signal_name = signal.Signals(signum).name
    except ValueError:
        signal_name = str(signum)
    logger.info("[退出] 收到信号：%s", signal_name)
    request_shutdown()


async def main(config_dir: str = None, port: int | None = None):
    global _main_loop, _shutdown_event

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    _setup_logger()
    logger = logging.getLogger(__name__)
    _main_loop = asyncio.get_running_loop()
    _shutdown_event = asyncio.Event()

    if config_dir is not None:
        config_dir = os.path.abspath(config_dir)

    app_config: AppConfig = configUtil.load(config_dir)
    llmApiUtil.init()
    demo_mode = app_config.setting.demo_mode

    _config_dir = config_dir or appPaths.CONFIG_DIR
    logger.info("[启动] 版本=v%s", __version__)
    logger.info("[启动] storage_root=%s | preset=%s", appPaths.STORAGE_ROOT, appPaths.PRESET_DIR)
    if demo_mode.read_only:
        logger.info("[启动] 演示模式已启用：freeze_data=true，系统将以只读浏览态启动")

    # 端口优先使用命令行参数，其次使用配置文件
    bind_host = app_config.setting.bind_host
    bind_port = port if port is not None else app_config.setting.bind_port
    logger.info("[启动] 监听地址：%s:%d", bind_host, bind_port)

    # ── 阶段 1：基础启动 ──────────────────────────────────────────────────────
    logger.info("[启动] 阶段 1/4：基础 service 启动")
    await messageBus.startup()
    await llmService.startup()
    await funcToolService.startup()
    await ormService.startup(app_config.setting.db_path)
    await persistenceService.startup()
    await agentService.startup()
    await roomService.startup()
    await schedulerService.startup()
    await presetService.startup()
    logger.info("[启动] 阶段 1/4 完成")

    # ── 阶段 2：导入配置 ──────────────────────────────────────────────────────
    logger.info("[启动] 阶段 2/4：导入 presets（RoleTemplate / Team / Dept / Room）")
    if demo_mode.read_only:
        logger.info("[启动] 演示模式已冻结数据，跳过 preset 导入")
    else:
        await presetService.import_from_app_config()
    logger.info("[启动] 阶段 2/4 完成")

    # ── 阶段 3：运行时构建 ────────────────────────────────────────────────────
    logger.info("[启动] 阶段 3/4：准备团队运行时恢复")
    logger.info("[启动] 阶段 3/4 完成")

    # ── 阶段 4：恢复状态 ──────────────────────────────────────────────────────
    logger.info("[启动] 阶段 4/4：恢复持久化状态")
    for team in await gtTeamManager.get_all_teams(enabled=True):
        await teamService.restore_team(
            team.id,
            workspace_root=app_config.setting.workspace_root,
            running_task_error_message="task interrupted by process restart",
        )
    logger.info("[启动] 阶段 4/4 完成")

    # ── 调度闸门：根据 LLM 配置状态决定是否开启调度 ──
    if demo_mode.read_only:
        schedulerService.stop_schedule("演示模式已冻结数据")
    else:
        await schedulerService.start_schedule()

    web_server = tornado.httpserver.HTTPServer(route.application)
    web_server.listen(bind_port, bind_host)

    try:
        await _shutdown_event.wait()
    finally:
        web_server.stop()
        schedulerService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        funcToolService.shutdown()
        roomService.shutdown()
        llmService.shutdown()
        await messageBus.shutdown()
        _remove_pid()
        _shutdown_event = None
        _main_loop = None


if __name__ == "__main__":
    _check_single_instance()
    _write_pid()
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=None, dest="config_dir", help="config 目录路径")
    parser.add_argument("--port", type=int, default=None, help="HTTP 监听端口（覆盖配置文件）")
    args = parser.parse_args()
    asyncio.run(main(config_dir=args.config_dir, port=args.port))
