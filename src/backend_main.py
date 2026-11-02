import argparse
import asyncio
import logging
import os
import signal
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import tornado.httpserver

from util import llmApiUtil, configUtil, logUtil
from util.configUtil.configTypes import AppConfig
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
import service.skillService as skillService
import appPaths
import route
from db import has_pending_migrations, migrate_database
from version import __version__
from dal.db import gtTeamManager


def _setup_logger() -> None:
    logUtil.init_backend_logger()


logger = logging.getLogger(__name__)


_RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../run")
_PID_FILE = os.path.join(_RUN_DIR, "backend.pid")
_shutdown_event: asyncio.Event | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class StartupCallbacks:
    """启动过程中的可选回调，供桌面外壳等外层入口观察启动阶段。

    默认全部为 None 时行为与原有启动流程完全一致。
    """
    on_migration_start: Callable[[], None] | None = None
    on_migration_end: Callable[[], None] | None = None
    on_ready: Callable[[str, int], None] | None = None


def _backup_database_before_migration(abs_db_path: str) -> str:
    """把 `abs_db_path` 备份到 `appPaths.DATA_DIR/backups`，返回备份文件路径。

    **为什么不复用 `ormService.backup_database()`**：它依赖 `ormService.get_db_path()`，
    而迁移恰好跑在 ORM 绑定数据库**之前**，此时 `get_db_path()` 返回 None 会直接
    `raise RuntimeError("ormService not started")`。改用 sqlite3 原生
    `Connection.backup()`：它在源库上开一个读事务做页级拷贝，会一并读到 WAL 里未落盘的
    内容，也不需要目标库预先建好表结构。

    命名沿用既有备份惯例（`data_20260705_194824_132501.db`）。

    失败时**不吞异常**：清理掉可能写了一半的备份文件后继续向上抛，由调用方中止启动
    —— 因为不支持降级兼容（方案 §6.4），备份是唯一的回滚路径，「没备份就迁移」不可接受。
    """
    backup_dir = os.path.join(appPaths.DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    stem, suffix = os.path.splitext(os.path.basename(abs_db_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = os.path.join(backup_dir, f"{stem}_{timestamp}{suffix or '.db'}")

    source_conn = sqlite3.connect(abs_db_path)
    try:
        target_conn = sqlite3.connect(backup_path)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    except BaseException:
        # 半截备份文件会误导后续「恢复备份」操作，且看起来像可用的回滚点，必须删掉
        try:
            os.remove(backup_path)
        except OSError:
            pass
        raise
    finally:
        source_conn.close()

    return backup_path


def _run_database_migrations(
    db_path: str,
    workspace_root: str,
    callbacks: StartupCallbacks | None,
) -> None:
    """在 ORM 绑定数据库前执行迁移。

    迁移本身由 db 模块负责；入口只做编排：确保目录存在、**迁移前备份数据库**，
    并在确有待执行迁移时触发桌面外壳的迁移开始/结束回调。

    备份时机与条件（方案 §6.4 项 2）：
    - 只在**确有**待执行迁移且**库文件已存在**时备份 —— 避免每次启动都堆备份，
      也避免全新安装（库还没建）时去备份一个不存在的文件；
    - 备份在 `migrate_database` **之前**；备份失败直接抛出、中止启动，
      不允许「没备份就迁移」。
    - 备份排在 `on_migration_start` **之前**：备份失败会中止启动，此时不该让桌面外壳
      先显示「正在迁移」再崩掉；代价是备份期间没有进度提示（实测大库秒级，可接受）。

    ⚠️ **`db_existed` 必须在 `has_pending_migrations()` 之前取**：
    后者经 `migration_status()` → `connect_sqlite()` 走 `os.makedirs` + `sqlite3.connect`，
    **会立刻把库文件建出来**并写入 `_migrations` 表。若把 `os.path.exists` 放在它后面，
    判断恒为 True，「全新安装不备份」就形同虚设 —— 全新安装用户会在没有任何数据可丢的
    情况下，因为 `backups/` 写入失败（磁盘满/权限/只读卷）而直接启动失败。
    """
    abs_db_path = os.path.abspath(db_path)
    db_existed = os.path.exists(abs_db_path)

    os.makedirs(os.path.dirname(abs_db_path), exist_ok=True)

    has_pending = has_pending_migrations(abs_db_path)

    if has_pending:
        if db_existed:
            backup_path = _backup_database_before_migration(abs_db_path)
            logger.info("[启动] 迁移前已备份数据库：source=%s backup=%s", abs_db_path, backup_path)
        if callbacks is not None and callbacks.on_migration_start is not None:
            callbacks.on_migration_start()

    migrate_database(abs_db_path, workspace_root=workspace_root, verbose=True)

    if has_pending and callbacks is not None and callbacks.on_migration_end is not None:
        callbacks.on_migration_end()


def _check_single_instance(ignore_pid: bool = False) -> None:
    if ignore_pid or appPaths.IS_DESKTOP_MODE or os.environ.get("TEAMAGENT_ENV") == "test":
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


def _write_pid(ignore_pid: bool = False) -> None:
    if ignore_pid or appPaths.IS_DESKTOP_MODE or os.environ.get("TEAMAGENT_ENV") == "test":
        return
    os.makedirs(_RUN_DIR, exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid() -> None:
    # 测试环境与桌面模式均不处理 PID 文件（桌面单实例由 Tauri 保证，且 Resources 目录只读）
    if appPaths.IS_DESKTOP_MODE or os.environ.get("TEAMAGENT_ENV") == "test":
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


def _detect_run_env() -> str:
    """检测当前运行环境：docker / desktop / mac_app / source。"""
    if appPaths.IS_DESKTOP_MODE:
        return "desktop"
    if os.environ.get("TOGOSPACE_RUN_ENV") == "docker":
        return "docker"
    if getattr(sys, "frozen", False):
        return "mac_app"
    return "source"


async def main(
    config_dir: str = None,
    port: int | None = None,
    callbacks: StartupCallbacks | None = None,
):
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
    run_env = _detect_run_env()
    logger.info("[启动] 版本=v%s | 运行环境=%s", __version__, run_env)
    logger.info("[启动] storage_root=%s | preset=%s", appPaths.STORAGE_ROOT, appPaths.PRESET_DIR)
    if demo_mode.read_only:
        logger.info("[启动] 演示模式已启用：freeze_data=true，系统将以只读浏览态启动")

    # 端口优先使用命令行参数，其次使用配置文件
    bind_host = app_config.setting.bind_host
    bind_port = port if port is not None else app_config.setting.bind_port
    logger.info("[启动] 监听地址：%s:%d", bind_host, bind_port)

    # ── 数据库迁移：先于所有 service 执行 ────────────────────────────────────
    _run_database_migrations(
        app_config.setting.db_path,
        app_config.setting.workspace_root,
        callbacks,
    )

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
    skillService.startup()
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
    try:
        web_server.listen(bind_port, bind_host)
        if callbacks is not None and callbacks.on_ready is not None:
            callbacks.on_ready(bind_host, bind_port)
        await _shutdown_event.wait()
    finally:
        web_server.stop()
        schedulerService.shutdown()
        # `agentService.shutdown()` 会关掉各 Driver 的 stdio 连接；自建容器以 gtsp
        # 为主进程运行，stdin 收到 EOF 后 gtsp 立即退出，容器随之停回 exited（§7）。
        # 所以这里**不需要**再扫描或批量 stop 容器：既不会拖慢停机，也不会留下
        # 「backend 说已停机、容器还在跑」的窗口。
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        funcToolService.shutdown()
        skillService.shutdown()
        roomService.shutdown()
        await presetService.shutdown()
        llmService.shutdown()
        await messageBus.shutdown()
        _remove_pid()
        _shutdown_event = None
        _main_loop = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=None, dest="config_dir", help="config 目录路径")
    parser.add_argument("--port", type=int, default=None, help="HTTP 监听端口（覆盖配置文件）")
    parser.add_argument("--ignore-pid", action="store_true", help="忽略 PID 文件检查，允许多实例运行")
    args = parser.parse_args()

    _check_single_instance(ignore_pid=args.ignore_pid)
    _write_pid(ignore_pid=args.ignore_pid)
    
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    
    asyncio.run(main(config_dir=args.config_dir, port=args.port))
