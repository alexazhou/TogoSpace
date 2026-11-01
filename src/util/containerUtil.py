"""容器命令行构造：docker 可执行文件探测与 gtsp 拉起 argv。

**只做纯函数，不碰 DB / 进程 / 容器状态**：本模块同时被 `agentService`（driver）
与 `containerService` 使用，避免 driver 为了拼一条命令而依赖容器编排服务。

两种拉起形态（§3.2 形态 A / B）不能互换：
- 自建容器：`docker start -a -i <id>`——容器本身就是「gtsp + tini」，attach 即
  接管 stdio，关 stdin 后 gtsp 退出、容器自然停回 exited；
- 外部容器：`docker exec -i <id> /tmp/gtsp ...`——外部容器的主进程必须保持运行，
  只能额外 exec 一个 gtsp 进程。

`docker create` 的 argv 不在这里：它与挂载、label、配额等容器语义强相关，仍由
`containerService.build_create_argv` 负责。
"""
from __future__ import annotations

import os
import shutil

from constants import CONTAINER_GTSP_PATH, CONTAINER_LOG_DIR

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


def resolve_docker_binary_path() -> str | None:
    """探测 docker 可执行文件：`which`（PATH）优先，其次常见默认安装路径。

    未找到返回 None，由调用方决定报错方式：service 层抛 `DockerCliError`
    （`docker_cli_not_found`），driver 直接转成工具层可见的 `TogoException`。
    """
    found = shutil.which("docker")
    if found:
        return found

    for candidate in _DEFAULT_BINARY_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def build_container_start_argv(container_id: str) -> list[str]:
    """自建容器启动 argv：`docker start -a -i <id>`。

    `-a`（attach）把容器 stdio 接到当前进程；`-i` 保持 stdin 打开。
    关掉 stdin 后容器内 gtsp 收到 EOF 退出 → tini 同步退出 → 容器回 `exited`。
    """
    return ["start", "-a", "-i", container_id]


def build_container_exec_argv(container_id: str) -> list[str]:
    """外部容器启动 argv：`docker exec -i <id> /tmp/gtsp --mode stdio ...`。

    外部容器的主进程必须保持运行，所以只能 `exec` 进一个额外进程，
    不能用 `start`。
    """
    return [
        "exec",
        "-i",
        container_id,
        CONTAINER_GTSP_PATH,
        "--mode", "stdio",
        "--log-path", CONTAINER_LOG_DIR,
    ]


def build_container_attach_argv(container_id: str, *, is_managed: bool) -> list[str]:
    """按容器类型构造 gtsp 拉起 argv（§3.2 形态 A / B）。

    自建容器用 `start -a -i`；外部容器用 `exec -i`。两种形态不能互换：对自建
    容器用 exec 会因容器未 running 而失败；对外部容器用 start 会二次启动主进程。
    """
    if is_managed:
        return build_container_start_argv(container_id)
    return build_container_exec_argv(container_id)
