"""桌面模式后端入口（Tauri sidecar）。

职责只有四件事，业务代码零改动：
1. 从环境变量读取桌面参数（缺失/非法时报错退出）
2. 在内存中覆盖 SettingConfig：强制只监听 127.0.0.1、强制开启 token 鉴权
3. 通过 stdout 单行 marker 向 Rust 外壳汇报启动阶段（迁移开始/结束、就绪、错误）
4. 监听 stdin EOF：父进程退出（含被 kill -9）时 OS 关闭管道，后端据此优雅停机

启动编排完全复用 backend_main.main()，本模块不复制任何业务启动流程。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Mapping, TextIO

import backend_main
from util import configUtil

MARKER_PREFIX = "[TOGO] "

DESKTOP_ENV_FLAG = "TOGOSPACE_DESKTOP"
DESKTOP_ENV_PORT = "TOGOSPACE_DESKTOP_PORT"
DESKTOP_ENV_TOKEN = "TOGOSPACE_DESKTOP_TOKEN"
DESKTOP_ENV_PARENT_PID = "TOGOSPACE_PARENT_PID"

DESKTOP_BIND_HOST = "127.0.0.1"

# 可预期的启动失败分类，供 Rust 侧按 code 展示不同文案。
ERROR_INVALID_ENV = 1
ERROR_LISTEN_FAILED = 2
ERROR_CONFIG_LOAD_FAILED = 3
ERROR_STARTUP_FAILED = 4

_logger = logging.getLogger(__name__)


class DesktopParamError(Exception):
    """桌面参数缺失或非法。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DesktopParams:
    port: int
    token: str
    parent_pid: int | None


# ── stdout marker 协议 ───────────────────────────────────────────────────────

def _emit_marker(payload: str) -> None:
    """输出单行 marker：固定前缀、不换行拆分、立即 flush。

    Rust 侧按行匹配该前缀驱动启动状态机，格式必须保持稳定。
    """
    print(f"{MARKER_PREFIX}{payload}", flush=True)
    _logger.info("%s%s", MARKER_PREFIX, payload)


def _single_line(message: str) -> str:
    return " ".join(str(message).splitlines())


def emit_migration_start() -> None:
    _emit_marker("MIGRATION_START")


def emit_migration_end() -> None:
    _emit_marker("MIGRATION_END")


def emit_ready(host: str, port: int) -> None:
    _emit_marker(f"READY url=http://{host}:{port}")


def emit_error(code: int, message: str) -> None:
    _emit_marker(f"ERROR code={code} message={_single_line(message)}")


# ── 参数读取 ─────────────────────────────────────────────────────────────────

def _read_desktop_params(env: Mapping[str, str] | None = None) -> DesktopParams:
    """从环境变量读取并校验桌面模式参数。"""
    values = os.environ if env is None else env

    if values.get(DESKTOP_ENV_FLAG) != "1":
        raise DesktopParamError(
            ERROR_INVALID_ENV,
            f"{DESKTOP_ENV_FLAG} 必须为 1（当前进程不是由桌面外壳拉起的）",
        )

    raw_port = (values.get(DESKTOP_ENV_PORT) or "").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise DesktopParamError(ERROR_INVALID_ENV, f"{DESKTOP_ENV_PORT} 不是合法端口: {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise DesktopParamError(ERROR_INVALID_ENV, f"{DESKTOP_ENV_PORT} 超出端口范围: {port}")

    token = (values.get(DESKTOP_ENV_TOKEN) or "").strip()
    if not token:
        raise DesktopParamError(ERROR_INVALID_ENV, f"{DESKTOP_ENV_TOKEN} 不能为空")

    parent_pid: int | None = None
    raw_parent_pid = (values.get(DESKTOP_ENV_PARENT_PID) or "").strip()
    if raw_parent_pid:
        try:
            parent_pid = int(raw_parent_pid)
        except ValueError:
            parent_pid = None  # 仅用于诊断，非法值忽略

    return DesktopParams(port=port, token=token, parent_pid=parent_pid)


# ── 配置覆盖 ─────────────────────────────────────────────────────────────────

def _apply_desktop_overrides(params: DesktopParams) -> bool:
    """在已加载的 AppConfig 上应用桌面模式强制项。

    - bind_host / bind_port 只影响本次进程（不落盘）：桌面模式不允许用户配置对外监听。
    - auth.enabled / auth.token 需要落盘，浏览器访问同一后端时也使用同一 token。

    Returns:
        是否写回了 setting.json（只有鉴权项确实变化时才写，避免每次启动都改文件）。
    """
    setting = configUtil.get_app_config().setting

    setting.bind_host = DESKTOP_BIND_HOST
    setting.bind_port = params.port

    auth_persisted_changed = False
    if not setting.auth.enabled:
        setting.auth.enabled = True
        auth_persisted_changed = True
    if setting.auth.token != params.token:
        setting.auth.token = params.token
        auth_persisted_changed = True

    if auth_persisted_changed:
        configUtil.update_setting(lambda current: None)
    return auth_persisted_changed


# ── stdin 存活探针 ───────────────────────────────────────────────────────────

def _install_stdin_probe(
    on_eof=backend_main.request_shutdown,
    stream: TextIO | None = None,
) -> threading.Thread:
    """启动 stdin 读取线程：读到 EOF 即请求后端优雅停机。

    这是跨平台不留孤儿进程的主机制：父进程退出/被强杀后 OS 关闭管道，
    子进程读到 EOF 后自行收尾，无需信号。
    """
    source = sys.stdin if stream is None else stream

    def _probe() -> None:
        try:
            while source.read(4096):
                pass
        except Exception:
            pass
        _logger.info("[退出] stdin 已关闭（父进程退出），请求后端停机")
        on_eof()

    thread = threading.Thread(target=_probe, name="desktop-stdin-probe", daemon=True)
    thread.start()
    return thread


# ── 启动编排 ─────────────────────────────────────────────────────────────────

def _build_callbacks() -> backend_main.StartupCallbacks:
    return backend_main.StartupCallbacks(
        on_migration_start=emit_migration_start,
        on_migration_end=emit_migration_end,
        on_ready=emit_ready,
    )


async def _run_backend(params: DesktopParams) -> None:
    task = asyncio.create_task(
        backend_main.main(port=params.port, callbacks=_build_callbacks())
    )
    # backend_main.main 在第一个 await 之前已设置 _main_loop / _shutdown_event；
    # 让出一轮事件循环后再启动探针，保证 EOF 触发的 request_shutdown 不会落空。
    await asyncio.sleep(0)
    _install_stdin_probe()
    await task


def main() -> int:
    try:
        params = _read_desktop_params()
    except DesktopParamError as exc:
        emit_error(exc.code, exc.message)
        return 2

    try:
        configUtil.load()
        _apply_desktop_overrides(params)
    except Exception as exc:  # noqa: BLE001 — 启动期任何配置异常都要转成可读错误
        emit_error(ERROR_CONFIG_LOAD_FAILED, f"配置加载失败: {exc}")
        return 2

    try:
        asyncio.run(_run_backend(params))
    except OSError as exc:
        emit_error(ERROR_LISTEN_FAILED, f"服务监听失败: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 — 启动期异常以 marker 上报给外壳
        _logger.exception("桌面模式后端启动失败")
        emit_error(ERROR_STARTUP_FAILED, f"后端启动失败: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
