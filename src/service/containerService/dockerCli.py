"""Docker CLI 适配层：路径探测、argv 执行、超时控制与错误分类。

设计依据：docs/design/2026-10-31-docker-agent-sandbox/step2_technical.md §3.3 / §3.6。

**只做「怎么调用 docker」，不做容器语义**：容器命名、配额、DB 编排都在 `core.py`。
这一层的存在意义是让上层能在单测里替换成一个假的 CLI，从而不需要真 Docker。

三条硬约束（每条都有对应的失败教训）：
1. **一律 argv 执行，禁止 `shell=True`**：容器名与镜像名里有 Agent 名（可能含中文、
   空格、各种 Unicode），拼 shell 字符串会被当成命令解释。§9 明确要求。
2. **每个调用都必须有超时**：`docker inspect` 在 daemon 卡死时会永久挂起，而状态
   查询在工具调用路径上，必须短超时（`CONTAINER_INSPECT_TIMEOUT_SEC = 3`）。
3. **子进程必须回收**：超时后只 `cancel()` 会留下僵尸进程，必须 kill + wait。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
from dataclasses import dataclass

from exception import TogoException

logger = logging.getLogger(__name__)

# Docker 未在 PATH 时的常见默认安装路径（按平台区分）。
# 桌面应用从 GUI 启动时 PATH 往往不含 /usr/local/bin，这里做兜底探测。
_DEFAULT_BINARY_CANDIDATES = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/usr/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
    "/Applications/OrbStack.app/Contents/MacOS/xbin/docker",
    "/usr/local/lib/docker/cli-plugins/docker",
)

# 错误码（§3.6 Docker 环境类）
ERROR_DOCKER_CLI_NOT_FOUND = "docker_cli_not_found"
ERROR_DOCKER_DAEMON_UNAVAILABLE = "docker_daemon_unavailable"

# daemon 不可用的 stderr 特征。Docker Desktop / OrbStack / podman 各自措辞不同，
# 这里只做「明显是连接不上守护进程」的匹配；真正的判定依据是退出码非 0 +
# 命令不是容器语义错误，所以宁可少匹配也不要误判容器业务错误。
_DAEMON_UNAVAILABLE_MARKERS = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "docker daemon is not running",
    "error during connect",
    "open //./pipe/docker_engine",
)

# stderr 截断长度：Docker 报错有时会带整段用法说明（几百行），
# 原样塞进工具返回值会淹没上下文。只保留开头这么多字符。
_STDERR_MAX_CHARS = 2000

# `ping` 的超时：只用于探测 daemon 可用性，必须短（跑在列表接口路径上）
_PING_TIMEOUT_SEC = 5


class DockerCliError(TogoException):
    """docker 命令执行失败（非 0 退出或超时）。

    上层据 `error_code` 分类；`stderr` / `returncode` 保留原始信息供日志与排障，
    不直接暴露给模型（`error_message` 里已做截断与脱敏）。
    """

    def __init__(
        self,
        error_message: str,
        error_code: str | None = None,
        *,
        returncode: int | None = None,
        stderr: str = "",
        argv: tuple[str, ...] = (),
    ) -> None:
        super().__init__(error_message, error_code)
        self.returncode = returncode
        self.stderr = stderr
        self.argv = argv


@dataclass(frozen=True)
class DockerResult:
    """一次 docker 调用的成功结果（只保留 stdout 与退出码）。"""

    stdout: str
    returncode: int


def _truncate(text: str, limit: int = _STDERR_MAX_CHARS) -> str:
    """截断过长输出，并明确标注被截断（不静默丢信息）。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（已截断，原长 {len(text)} 字符）"


def _looks_like_daemon_unavailable(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return any(marker in lowered for marker in _DAEMON_UNAVAILABLE_MARKERS)


class DockerCli:
    """Docker 命令行调用的薄封装。

    实例持有解析到的 docker 可执行文件路径；路径解析在构造时完成，失败抛
    `docker_cli_not_found`。上层（`containerService`）按需创建单例。
    """

    def __init__(self, binary_path: str | None = None) -> None:
        self._binary_path = binary_path or self._resolve_binary_path()

    @property
    def binary_path(self) -> str:
        return self._binary_path

    @staticmethod
    def _resolve_binary_path() -> str:
        """探测 docker 可执行文件：`which`（PATH）优先，其次常见默认安装路径。

        找不到时抛 `docker_cli_not_found`——这是环境问题，不重试、不猜测。
        """
        found = shutil.which("docker")
        if found:
            return found

        for candidate in _DEFAULT_BINARY_CANDIDATES:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        raise DockerCliError(
            "未找到 docker 命令，请确认 Docker 已安装并在 PATH 中",
            ERROR_DOCKER_CLI_NOT_FOUND,
        )

    async def run(
        self,
        args: list[str],
        *,
        timeout_sec: float,
        stdin_data: bytes | None = None,
    ) -> DockerResult:
        """执行 `docker <args...>`，成功返回 `DockerResult`，失败抛 `DockerCliError`。

        Args:
            args: 不含 `docker` 本身的参数列表（每个元素原样作为 argv 一项）。
            timeout_sec: 超时秒数；超时会 kill 子进程后抛错。
            stdin_data: 需要从 stdin 送入的字节（如 `docker exec -i` 的初始输入）。

        Raises:
            DockerCliError: 非 0 退出、超时、或 daemon 不可用。
        """
        argv = (self._binary_path, *args)
        logger.debug("docker 调用: %s", args)

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # 禁止 shell：见模块 docstring。
            # 独立进程组：超时要按组杀，否则 docker 派生的孙进程仍持有 stdout
            # 管道，`process.wait()` 会一直等不到 EOF（实测会多挂一个 wait 超时）。
            start_new_session=True,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=stdin_data),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            # 超时：只 cancel 会留下僵尸，必须显式 kill 并 wait 回收。
            await self._terminate(process)
            raise DockerCliError(
                f"docker 命令执行超时（{timeout_sec}s）: {' '.join(args)}",
                None,
                argv=argv,
            ) from None
        except asyncio.CancelledError:
            # 上层取消（如工具调用被中断）：同样不能留下孤儿进程。
            await self._terminate(process)
            raise

        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

        if process.returncode != 0:
            message = _truncate(stderr) or f"docker 退出码 {process.returncode}"
            error_code = (
                ERROR_DOCKER_DAEMON_UNAVAILABLE
                if _looks_like_daemon_unavailable(stderr)
                else None
            )
            raise DockerCliError(
                f"docker {' '.join(args[:2])} 失败: {message}",
                error_code,
                returncode=process.returncode,
                stderr=stderr,
                argv=argv,
            )

        return DockerResult(stdout=stdout, returncode=process.returncode)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """尽力杀死子进程组并回收，任何异常都不外抛（清理路径不能盖住原始错误）。

        必须按**进程组**杀（`start_new_session=True` 让子进程成为组长）：
        只 `process.kill()` 会留下 docker 派生的孙进程，它仍持有 stdout / stderr
        管道写端，`process.wait()` 等不到 EOF，于是清理自己又挂满超时。
        """
        # 先整组 SIGKILL：gs_id 由 start_new_session 保证等于子进程 pid
        try:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            logger.warning("docker 子进程组 kill 失败", exc_info=True)

        # 再兜一层单进程 kill：进程组创建失败（老平台）时的退路
        try:
            if process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        except Exception:
            logger.warning("docker 子进程 kill 失败", exc_info=True)

        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("docker 子进程回收超时")
        except Exception:
            logger.warning("docker 子进程回收失败", exc_info=True)

    async def ping(self) -> bool:
        """探测 daemon 是否可用（`docker version --format` 比 `docker info` 快）。

        用于 `list_containers` 的 `docker_available` 字段：不可用不算错误，
        只是把该字段置 false，不抛异常。
        """
        try:
            await self.run(
                ["version", "--format", "{{.Server.Version}}"],
                timeout_sec=_PING_TIMEOUT_SEC,
            )
            return True
        except DockerCliError:
            return False
