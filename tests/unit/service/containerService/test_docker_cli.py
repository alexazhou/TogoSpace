"""`containerService.dockerCli` 的单元测试。

用**假的 docker 可执行脚本**驱动真实子进程，而不是 mock `asyncio.create_subprocess_exec`：
本模块的全部价值就是「正确地把 argv 交给子进程并把 stderr 分类」，把它 mock 掉
等于只测自己的 if/else。假脚本按参数分派并输出预设的 stdout / stderr / 退出码，
因此路径探测、argv 传参、超时 kill、stderr 分类这些**真实行为**都被覆盖。

不做真实 Docker 的理由：本机是 OrbStack（≠ Docker Desktop，见任务 #109 事件 344），
`container_mount_denied` 这类用例在这里不可能真实复现；这些用例在集成 / e2e 层
标 skip 并写明前置条件，绝不在单测里造假。
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap

import pytest

from service.containerService import dockerCli
from service.containerService.dockerCli import DockerCli, DockerCliError


def _write_script(path, body: str) -> str:
    """写一个可执行的假 docker 脚本，返回其路径。"""
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def fake_docker(tmp_path):
    """默认的假 docker：把收到的 argv 原样回显到 stdout，退出码 0。

    回显让用例能断言「到底把什么参数交给了 docker」——这正是 argv 构造的验收点。
    """
    script = tmp_path / "docker"
    return _write_script(
        script,
        """\
        # 回显全部参数（每行一个），便于断言 argv
        for arg in "$@"; do
          echo "$arg"
        done
        exit 0
        """,
    )


# ---------------------------------------------------------------------------
# 路径探测
# ---------------------------------------------------------------------------


def test_explicit_binary_path_is_used_as_is(tmp_path, fake_docker):
    """显式传入的路径优先，不再做探测（测试与集成时替换用）。"""
    cli = DockerCli(binary_path=fake_docker)

    assert cli.binary_path == fake_docker


def test_missing_docker_reports_docker_cli_not_found(tmp_path, monkeypatch):
    """PATH 与常见默认路径都找不到 docker 时，报 `docker_cli_not_found`。

    这条不是「异常类型对不对」的形式检查：工具层要靠这个 error_code 提示用户
    「请安装 Docker」，而不是给一句看不懂的子进程错误。
    """
    monkeypatch.setattr(dockerCli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(dockerCli.os.path, "isfile", lambda _path: False)

    with pytest.raises(DockerCliError) as excinfo:
        DockerCli()

    assert excinfo.value.error_code == dockerCli.ERROR_DOCKER_CLI_NOT_FOUND
    assert "docker" in str(excinfo.value)


def test_binary_resolved_from_path_first(tmp_path, monkeypatch):
    """PATH 里能找到时优先用 PATH 的结果（不猜默认安装路径）。"""
    found = _write_script(tmp_path / "docker", "exit 0\n")
    monkeypatch.setattr(dockerCli.shutil, "which", lambda _name: found)

    assert DockerCli().binary_path == found


def test_binary_falls_back_to_default_candidate_paths(tmp_path, monkeypatch):
    """PATH 里没有时回退探测默认安装路径（GUI 启动的桌面应用 PATH 往往不含 docker）。"""
    candidate = _write_script(tmp_path / "docker", "exit 0\n")
    monkeypatch.setattr(dockerCli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        dockerCli, "_DEFAULT_BINARY_CANDIDATES", (str(tmp_path / "missing"), candidate)
    )

    assert DockerCli().binary_path == candidate


# ---------------------------------------------------------------------------
# argv 执行：不回显、不解释、原样传参
# ---------------------------------------------------------------------------


async def test_run_passes_argv_verbatim_without_shell_interpretation(fake_docker):
    """参数原样作为 argv 传递：含空格 / 中文 / `$` / `;` 的值不能被 shell 解释。

    这是 §9 的硬要求。若实现用了 `shell=True`，`$(touch /tmp/x)` 会被求值，
    而这里断言它只是普通字符串被回显出来。
    """
    cli = DockerCli(binary_path=fake_docker)
    dangerous = ["inspect", "容器 名; touch /tmp/pwned", "$(echo injected)"]

    result = await cli.run(dangerous, timeout_sec=10)

    assert result.stdout.splitlines() == dangerous
    assert not os.path.exists("/tmp/pwned")


async def test_run_returns_stdout_and_zero_returncode(fake_docker):
    cli = DockerCli(binary_path=fake_docker)

    result = await cli.run(["version"], timeout_sec=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "version"


async def test_run_raises_with_stderr_on_nonzero_exit(tmp_path):
    """非 0 退出：异常里要带 stderr 原文与退出码（上层据此分类与提示）。"""
    script = _write_script(
        tmp_path / "docker",
        """\
        echo "Error response from daemon: No such container: abc" >&2
        exit 1
        """,
    )
    cli = DockerCli(binary_path=script)

    with pytest.raises(DockerCliError) as excinfo:
        await cli.run(["rm", "-f", "abc"], timeout_sec=10)

    assert excinfo.value.returncode == 1
    assert "No such container" in excinfo.value.stderr
    assert "No such container" in str(excinfo.value)


async def test_stderr_is_truncated_in_message_but_full_stderr_kept(tmp_path):
    """超长 stderr（Docker 用法说明可能几百行）在消息里截断，原文保留在属性上。

    不收口的话，一整篇 usage 会被塞进模型上下文，把真正的错误淹没。
    """
    script = _write_script(
        tmp_path / "docker",
        f"""\
        # 输出 5000 字符
        i=0
        while [ $i -lt 100 ]; do
          echo "usage line $i of a very long docker error message" >&2
          i=$((i+1))
        done
        exit 1
        """,
    )
    cli = DockerCli(binary_path=script)

    with pytest.raises(DockerCliError) as excinfo:
        await cli.run(["run"], timeout_sec=10)

    message = str(excinfo.value)
    assert "已截断" in message
    assert len(message) < dockerCli._STDERR_MAX_CHARS + 200
    # 完整原文仍可通过属性拿到（排障用）
    assert len(excinfo.value.stderr) > dockerCli._STDERR_MAX_CHARS


# ---------------------------------------------------------------------------
# 超时与取消：子进程必须被回收
# ---------------------------------------------------------------------------


async def test_timeout_kills_child_process_and_raises(tmp_path):
    """超时必须 kill 子进程并立刻抛错，不能只是停止等待（否则留下僵尸）。

    超时值取 2s：要留出 shell 启动 + 写 pid 文件的时间，否则用例本身会不稳
    （0.3s 时进程还没起来就被 kill，pid 文件根本不会出现，测的就不是回收能力了）。
    """
    pid_file = tmp_path / "child.pid"
    script = _write_script(
        tmp_path / "docker",
        f"""\
        echo $$ > {pid_file}
        sleep 30
        exit 0
        """,
    )
    cli = DockerCli(binary_path=script)

    with pytest.raises(DockerCliError) as excinfo:
        await cli.run(["info"], timeout_sec=2)

    assert "超时" in str(excinfo.value)

    # 子进程必须已经死掉：kill -0 对它应当失败
    assert pid_file.exists(), "假脚本未启动"
    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_cancellation_kills_child_process(tmp_path):
    """上层取消（工具调用被中断）时同样要回收子进程，不留孤儿。"""
    import asyncio

    pid_file = tmp_path / "child.pid"
    script = _write_script(
        tmp_path / "docker",
        f"""\
        echo $$ > {pid_file}
        sleep 30
        exit 0
        """,
    )
    cli = DockerCli(binary_path=script)

    task = asyncio.create_task(cli.run(["info"], timeout_sec=60))
    # 等假脚本真正启动
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# ---------------------------------------------------------------------------
# 错误分类：daemon 不可用要能被识别出来（与「容器业务错误」区分）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr_line",
    [
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        "error during connect: Get http://%2Fvar%2Frun%2Fdocker.sock/v1.24/version: dial unix: connect: no such file or directory",
        "open //./pipe/docker_engine: The system cannot find the file specified.",
    ],
)
async def test_daemon_unavailable_stderr_maps_to_daemon_error_code(tmp_path, stderr_line):
    """三种平台的 daemon 不可用措辞都要归到 `docker_daemon_unavailable`。

    上层据此决定「保留 DB 记录、提示用户启动 Docker」，若误判成容器不存在，
    会把记录软删除，用户启动 Docker 后容器就再也找不回来了。
    """
    script = _write_script(
        tmp_path / "docker",
        f"""\
        cat >&2 <<'EOF'
        {stderr_line}
        EOF
        exit 1
        """,
    )
    cli = DockerCli(binary_path=script)

    with pytest.raises(DockerCliError) as excinfo:
        await cli.run(["inspect", "abc"], timeout_sec=10)

    assert excinfo.value.error_code == dockerCli.ERROR_DOCKER_DAEMON_UNAVAILABLE


async def test_container_business_error_is_not_misclassified_as_daemon(tmp_path):
    """容器业务错误（no such container）不能带 daemon 错误码。"""
    script = _write_script(
        tmp_path / "docker",
        """\
        echo "Error response from daemon: No such container: abc" >&2
        exit 1
        """,
    )
    cli = DockerCli(binary_path=script)

    with pytest.raises(DockerCliError) as excinfo:
        await cli.run(["inspect", "abc"], timeout_sec=10)

    assert excinfo.value.error_code is None


# ---------------------------------------------------------------------------
# ping：不可用不是异常，只是 False
# ---------------------------------------------------------------------------


async def test_ping_true_when_version_succeeds(tmp_path):
    script = _write_script(tmp_path / "docker", "echo 27.0.3\nexit 0\n")
    cli = DockerCli(binary_path=script)

    assert await cli.ping() is True


async def test_ping_false_when_daemon_down(tmp_path):
    """`list_containers` 要靠这个返回 False 而不是抛异常（不可用只是状态字段）。"""
    script = _write_script(
        tmp_path / "docker",
        """\
        echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock." >&2
        exit 1
        """,
    )
    cli = DockerCli(binary_path=script)

    assert await cli.ping() is False


async def test_ping_returns_false_on_timeout_without_hanging(tmp_path):
    """daemon 卡死时 ping 必须靠超时收口返回 False，不能永久挂住。

    它跑在列表接口路径上，挂住会让整个 Agent 列表页转圈。这里用 8s 的脚本 +
    断言「在 ping 自身超时内返回」，超时值取 `_PING_TIMEOUT_SEC`（实现里的常量），
    而不是写死一个魔数——否则实现改了超时，用例还是绿的。
    """
    import time

    script = _write_script(tmp_path / "docker", "sleep 30\n")
    cli = DockerCli(binary_path=script)

    started = time.monotonic()
    assert await cli.ping() is False
    elapsed = time.monotonic() - started

    assert elapsed < dockerCli._PING_TIMEOUT_SEC + 5, (
        f"ping 耗时 {elapsed:.1f}s，说明超时没有生效"
    )
