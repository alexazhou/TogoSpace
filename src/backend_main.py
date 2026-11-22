import argparse
import asyncio
import ipaddress
import logging
import os
import signal
import sys
from dataclasses import dataclass
from typing import Callable

# litellm 在 import 时会同步请求远程价格表（超时 5s）；这里改用包内自带副本。
# 必须放在导入 util/service（会间接 import litellm）之前。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

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
from db import DatabaseAheadOfCodeError, has_pending_migrations, migrate_database
from version import __version__
from dal.db import gtTeamManager


def _setup_logger() -> None:
    logUtil.init_backend_logger()


_RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../run")
_PID_FILE = os.path.join(_RUN_DIR, "backend.pid")
_shutdown_event: asyncio.Event | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def _is_loopback_bind(host: str) -> bool:
    """`localhost` 与回环地址（127.x / ::1）都算回环。解析失败按非回环处理（宁多勿漏）。"""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _warn_exposure_if_unsafe(bind_host: str, auth_enabled: bool) -> None:
    """裸跑形态暴露面告警（#277 追加，Operator 20:30 决策 A）。

    只做「可发现」：**实际 bind 非回环且 `auth.enabled=false`** ⇒ 打一条醒目告警，
    **不阻断启动**、不改默认值、不改鉴权行为、不加配置项。告警只描述形态，
    **不带 token 或任何凭证值**（判据 #1694：告警行 0 命中凭证）。
    桌面端（desktopEntry.py）强制 127.0.0.1 + auth.enabled=True，天然不触发。
    """
    if _is_loopback_bind(bind_host) or auth_enabled:
        return
    logging.getLogger(__name__).warning(
        "[安全告警] 后端监听 %s 且未开启鉴权，同网络内其他机器可直接访问，"
        "包括 MCP 凭证接口；如非必要请改为 127.0.0.1 或开启 auth。",
        bind_host,
    )


@dataclass(frozen=True)
class StartupCallbacks:
    """启动过程中的可选回调，供桌面外壳等外层入口观察启动阶段。

    默认全部为 None 时行为与原有启动流程完全一致。
    """
    on_migration_start: Callable[[], None] | None = None
    on_migration_end: Callable[[], None] | None = None
    on_ready: Callable[[str, int], None] | None = None


def _run_database_migrations(
    db_path: str,
    workspace_root: str,
    callbacks: StartupCallbacks | None,
) -> None:
    """在 ORM 绑定数据库前执行迁移。

    迁移本身由 db 模块负责；入口只做编排：确保目录存在，并在确有
    待执行迁移时触发桌面外壳的迁移开始/结束回调。
    """
    abs_db_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_db_path), exist_ok=True)

    has_pending = has_pending_migrations(abs_db_path)
    if has_pending and callbacks is not None and callbacks.on_migration_start is not None:
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
        # 只在**真正 bind 成功之后**打这行（#274 N-3）：迁移守卫拒绝启动时不该出现任何
        # 「监听/已就绪」字样，否则 `grep 监听地址` 会把它误读成「已经起来了」。
        logger.info("[启动] 已监听：%s:%d（HTTP 服务已就绪）", bind_host, bind_port)
        # 出口告警（#277 F1）：bind 非回环 + 未开鉴权 ⇒ 提醒暴露面（不阻断启动）。
        _warn_exposure_if_unsafe(bind_host, app_config.setting.auth.enabled)
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

    try:
        asyncio.run(main(config_dir=args.config_dir, port=args.port))
    except DatabaseAheadOfCodeError as exc:
        # 库比代码新（#270）：拒绝启动，不进入服务状态。用非 0 退出码 + 可读信息，
        # 而不是让它掉进通用异常里变成一段 traceback（信息在、但不便于人看）。
        print(f"[TOGO] ERROR {exc}", file=sys.stderr)
        sys.exit(2)
