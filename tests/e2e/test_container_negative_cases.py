"""#114：本机不可复现的容器负向用例（OrbStack 环境）

## 这组用例为什么默认不跑

1. **`container_mount_denied` 在本机不可能真实触发**。它的前置条件是
   **Docker Desktop 且工作目录不在 File sharing 白名单里**。本机是 OrbStack
   （`docker info` = OrbStack / aarch64 / 29.4.0），实测 `/tmp`、`/Volumes/PDATA`、
   `/private/var/tmp`、`/System/Volumes/Data/Users` 全部挂载成功。
   要让它「跑起来」只能 mock 掉 docker 包装层，那测的是我自己的 mock（事件 344 明确禁止）。

2. **两个用例都会真的 create/start/rm 容器**。所以即便 `container_mount_denied`
   在别的机器上可复现，也不该让 `pytest tests` 的默认结果依赖「本机有没有 Docker /
   镜像拉没拉」。闸门与 `test_container_happy_path.py` 同口径：
   `TOGOSPACE_TEST_DOCKER=1` 才跑。

> ⚠️ 历史教训（G1 期间踩过）：这两个用例一度用**无条件** `@pytest.mark.skip` 拦住。
> 无条件 skip **不会**因为实现修好而自动解除——G1 落地那天它仍然是 skip，
> 「修好就自动绿」这句话是错的。现在改成可判定的 `skipif(<开闸变量>)`：
> skip 的理由变成「这是真实 Docker 用例」，而不是「实现没修」。

## G1 已落地（原「实现缺口」记录，保留作背景）

`step1_product.md:119` 验收项要求：无 bash 镜像「能建、能进、文件工具可用，
`execute_bash` 返回**明确原因与出路**」。修复前 `execute_bash` 返回 gtsp 原始错误
```
{"code":"server/internal_error",
 "error":"failed to start command: exec: \\"bash\\": executable file not found in $PATH"}
```
现由 `tspDriver._execute_tsp_tool` 识别（工具名限定 `execute_bash` + 完整 exec 特征），
在**保留**协议层 `code` 的同时补出业务层 `error_code=container_missing_bash`
与含「原因 + 出路」的 `message`。

`test_no_bash_image_reports_actionable_reason` 的断言当初是**按文档要求**写的
（不是按现状写），所以它在 G1 修好前必然红 —— 这是它当初保持 skip 的第二个原因。
断言本身一个字没改（除常量名），修复后转绿。

## 环境口径（事件 343 / 344 + 小马哥 2026-11-01 裁定）

- alpine 与 distroless **归为一类：无 bash 镜像**。
  **不是**「musl 不行 / glibc 行」——`gtsp-linux-arm64` 是静态链接 Go 二进制
  （`file` 确认无动态依赖），`gtsp --version` 在 alpine / ubuntu / debian 三镜像
  全部 `v0.4.17` 正常。`gcr.io/distroless/base-debian12` 是 glibc 且同样没 bash
  ⇒ 因果与 libc 无关，`step2_technical.md:104`「兼容所有 Linux 发行版（含 Alpine
  等 musl libc 镜像）」本来就写对了。
- 不做静态产物替换、不加 Alpine 特判、不改设计文档、不给 alpine 造 `sh` 顶替。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from constants import DriverType, ExecEnv
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from service import containerService, roomService
from service.containerService import core as container_core
from service.containerService.dockerCli import (
    ERROR_DOCKER_CLI_NOT_FOUND,
    DockerCli,
    DockerCliError,
)

# ---------------------------------------------------------------------------
# 前置条件：两个用例都需要本机不具备的环境，因此无条件 skip
# ---------------------------------------------------------------------------

_MOUNT_DENIED_SKIP = pytest.mark.skip(
    reason=(
        "前置条件：Docker Desktop（macOS/Windows）且容器要挂载的工作目录**未**加入 "
        "File sharing 白名单。此时 docker create 返回 "
        "`is not shared from the host` / `mounts denied` 类错误。"
        "本机是 OrbStack 29.4.0（不是 Docker Desktop），挂载源路径不做白名单限制，"
        "实测 /tmp、/Volumes/PDATA、/private/var/tmp、/System/Volumes/Data/Users "
        "全部挂载成功，该场景不可复现 ⇒ skip，不 mock docker 包装层（事件 344）。"
        "换到 Docker Desktop 且不共享工作目录的机器上，去掉本 skip 即可直接跑。"
    )
)

# 真实 Docker e2e 闸门：**这两个用例会真的 create/start/rm 容器**，所以默认不跑。
# 与 `test_container_happy_path.py` 同口径（`TOGOSPACE_TEST_DOCKER=1`）。
#
# G1 落地后 `_NO_BASH_SKIP` 必须摘掉（否则修好了也永远 skip），但摘掉后这两个用例
# 就会在**默认口径**下真起容器 —— 那样 `pytest tests` 的默认结果会依赖本机有没有
# Docker、镜像拉没拉，报数失去可比性。所以 skip 的**理由**从「实现没修」换成
# 「这是真实 Docker 用例」，闸门形式从无条件 skip 换成可判定的 skipif。
DOCKER_E2E_ENABLED = os.environ.get("TOGOSPACE_TEST_DOCKER") == "1"

_DOCKER_E2E_SKIP = pytest.mark.skipif(
    not DOCKER_E2E_ENABLED,
    reason=(
        "真实 Docker e2e：会 create/start/rm 真实容器（alpine:3.20 / distroless）。"
        "需显式开闸 TOGOSPACE_TEST_DOCKER=1 才会运行"
    ),
)

_MIGRATION_DIR = Path(__file__).resolve().parents[2] / "assets" / "migrate"

# 与 happy path 同口径：只建容器服务真正依赖的两张业务表，容器表走真实 0017 迁移。
# 不复用 `test_container_happy_path._MIN_SCHEMA_SQL`：跨测试文件 import 私有常量
# 会把别人的类型问题带进来，且漂移风险由 happy path 末尾那条防漂移用例统一兜住。
_MIN_SCHEMA_SQL = """
CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    uuid TEXT UNIQUE,
    config TEXT NOT NULL DEFAULT '{}',
    i18n TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME, updated_at DATETIME
);
CREATE TABLE agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    role_template_id INTEGER NOT NULL,
    employ_status TEXT NOT NULL DEFAULT 'ON_BOARD',
    model TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL DEFAULT 'NATIVE',
    employee_number INTEGER NOT NULL DEFAULT 0,
    i18n TEXT NOT NULL DEFAULT '{}',
    allow_tools TEXT, allow_skills TEXT,
    created_at DATETIME, updated_at DATETIME
);
"""

_ALPINE_IMAGE = "alpine:3.20"
_DISTROLESS_IMAGE = "gcr.io/distroless/base-debian12"


@dataclass
class NegativeEnv:
    """负向用例的环境句柄；`created` 用于兜底清理真实容器。"""

    cli: DockerCli
    workspace: Path
    team_dir: Path
    created: list[str] = field(default_factory=list)

    def track(self, record) -> None:
        if record.docker_id:
            self.created.append(record.docker_id)


@pytest.fixture(scope="module")
def real_docker():
    """真实 DockerCli；本机没有 docker / daemon 时整组 skip（不是 fail）。"""
    try:
        cli = DockerCli()
    except DockerCliError as exc:
        pytest.skip(f"本机没有可用的 Docker CLI: {exc.error_message}")
    container_core.reset_cli_for_test(cli)
    try:
        yield cli
    finally:
        container_core.reset_cli_for_test(None)


@pytest.fixture
async def neg_env(tmp_path, monkeypatch, real_docker):
    """最小运行环境：临时库 + 临时 workspace + 真实 docker。"""
    from model.dbModel.base import bind_database, unbind_database
    from service.ormService import AioSqliteDatabase

    db_path = tmp_path / "neg.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_MIN_SCHEMA_SQL)
        conn.executescript((_MIGRATION_DIR / "0017_agent_containers.sql").read_text("utf-8"))
        conn.commit()
    finally:
        conn.close()

    database = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(database)
    await database.aio_connect()

    workspace = tmp_path / "ws"
    team_dir = workspace / "neg-team"
    (team_dir / "agent" / "小美").mkdir(parents=True)
    (team_dir / "shared").mkdir(parents=True)

    monkeypatch.setattr(
        container_core.workspaceUtil,
        "resolve_team_workdir",
        lambda team_name, team_config, workspace_root: str(team_dir),
    )
    monkeypatch.setattr(
        container_core.workspaceUtil,
        "resolve_shared_workdir",
        lambda team_workdir: str(Path(team_workdir) / "shared"),
    )
    monkeypatch.setattr(
        container_core.workspaceUtil,
        "resolve_agent_workdir",
        lambda team_workdir, agent_name: str(Path(team_workdir) / "agent" / str(agent_name)),
    )
    monkeypatch.setattr(
        container_core.workspaceUtil, "resolve_workspace_root", lambda _root: str(workspace)
    )

    await GtTeam(name="neg-team", config={}, i18n={}).aio_save(force_insert=True)
    await GtAgent(
        team_id=1, name="小美", role_template_id=1, i18n={"display_name": "小美"}
    ).aio_save(force_insert=True)

    env = NegativeEnv(cli=real_docker, workspace=workspace, team_dir=team_dir)
    try:
        yield env
    finally:
        # 兜底清理：按 docker_id 删，绝不用名字（容器名跨 Team 可能重名）
        for docker_id in env.created:
            try:
                await real_docker.run(["rm", "-f", docker_id], timeout_sec=60)
            except Exception:  # noqa: BLE001 - 清理失败不能盖住用例结果
                pass
        await database.aio_close()
        unbind_database()


# ---------------------------------------------------------------------------
# ① 挂载被拒 ≠ 容器不存在（skip：需 Docker Desktop 未共享工作目录）
# ---------------------------------------------------------------------------


@_MOUNT_DENIED_SKIP
@_DOCKER_E2E_SKIP
async def test_mount_denied_is_reported_as_mount_denied_not_as_container_missing(neg_env):
    """File sharing 拒绝挂载时，错误码必须是 `container_mount_denied`。

    **这条用例真正要钉住的语义**：挂载被拒绝，和「容器不存在」是两件不同的事。
    报成 `container_not_found` 会诱导模型去删记录重建容器（无效动作，重建还是挂不上），
    报成 `container_create_failed` 则让模型以为是自己参数写错了（会去换镜像重试，也无效）。
    只有 `container_mount_denied` 指向真正的出路：**把工作目录加进 Docker Desktop 的
    File sharing 白名单**。

    `_classify_docker_error(exc, fallback)` 的 fallback 在创建路径上是
    `ERROR_CONTAINER_CREATE_FAILED`，所以「退化成 create_failed」是本用例的主要防线。
    """
    # 前置：本机确实不是 Docker Desktop，或者工作目录确实没被共享。
    # 真跑时这里会抛 `is not shared from the host` 类的 DockerCliError，
    # 被 create_container 归类后抛 TogoException。
    with pytest.raises(TogoException) as excinfo:
        await containerService.create_container(1, image=containerService.DEFAULT_CONTAINER_IMAGE)

    exc = excinfo.value
    # ① 错误码必须是挂载被拒，不是「容器不存在」，也不是「创建失败」的兜底码
    assert exc.error_code == containerService.ERROR_CONTAINER_MOUNT_DENIED, (
        f"挂载被拒必须报 {containerService.ERROR_CONTAINER_MOUNT_DENIED}，"
        f"实得 {exc.error_code}。"
    )
    assert exc.error_code != containerService.ERROR_CONTAINER_NOT_FOUND, (
        "把挂载被拒报成「容器不存在」会让模型去删记录重建，而重建仍会撞同一堵墙。"
    )
    assert exc.error_code != containerService.ERROR_CONTAINER_CREATE_FAILED, (
        f"落到了 {containerService.ERROR_CONTAINER_CREATE_FAILED} 兜底码，"
        "说明 `_MOUNT_DENIED_MARKERS` 没匹配上真实 stderr 特征 —— "
        "这是分类器与真机文案脱节，不是用例问题。"
    )

    # ② 报错信息要能指导动作：提到「共享 / 授权 / File sharing」这类可执行方向
    message = str(exc).lower()
    assert any(
        token in message for token in ("shared", "file sharing", "mounts denied", "permission")
    ), f"错误信息没给出路，模型无从下手：{exc}"

    # ③ 失败必须补偿干净：不留 DB 记录、不留真实容器（§5.3）
    #    「挂载失败」最容易漏补偿 —— 因为 docker create 本身可能已返回 container id
    assert await containerService.list_agent_containers(1) == [], (
        "创建失败后不该留下活跃记录：预记录必须被物理删除，否则白占配额、"
        "且下次创建会因名称被占而报 conflict。"
    )
    assert neg_env.created == [], "本用例不该产生成功创建的容器"


# ---------------------------------------------------------------------------
# ② 无 bash 镜像：容器可用，但 execute_bash 必须给「明确原因 + 出路」（G1 已落地）
# ---------------------------------------------------------------------------


@_DOCKER_E2E_SKIP
@pytest.mark.parametrize("image", [_ALPINE_IMAGE, _DISTROLESS_IMAGE])
async def test_no_bash_image_reports_actionable_reason(neg_env, image):
    """无 bash 镜像：能建、能进、文件工具可用；`execute_bash` 报原因与出路。

    **参数化的两个镜像属于同一类**（无 bash），不是「musl vs glibc」两类：
    - `alpine:3.20` —— musl libc，无 bash
    - `gcr.io/distroless/base-debian12` —— **glibc**，同样无 bash

    后者是这条用例的关键：它证明「没 bash 就挂」与 libc 无关。
    `gtsp-linux-arm64` 是静态链接 Go 二进制，在 alpine 上 `--version` 正常。

    断言按 `step1_product.md:45` 写（不是按现状写）：
    > 镜像里必须有 `bash` 才能用 `execute_bash`。此时文件工具正常，`execute_bash`
    > 会明确提示「镜像内没有 bash」，并给出出路：换镜像，或在容器内装 bash。
    > 系统不会偷偷用 `sh` 代替。
    """
    from service.agentService.agentHistoryStore import AgentHistoryStore
    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.tspDriver import TspAgentDriver
    from service.agentService.toolRegistry import AgentToolRegistry

    # —— 能建：无 bash 不影响 docker create
    record = await containerService.create_container(1, image=image)
    neg_env.track(record)
    assert record.docker_id is not None

    @dataclass
    class _Host:
        gt_agent: GtAgent = field(
            default_factory=lambda: GtAgent(id=1, team_id=1, name="小美", role_template_id=1)
        )
        system_prompt: str = ""
        agent_workdir: str = ""
        _history: AgentHistoryStore = field(default_factory=lambda: AgentHistoryStore(agent_id=1))
        tool_registry: AgentToolRegistry = field(default_factory=AgentToolRegistry)

        async def execute_pending_tools(self) -> None:
            return None

    host = _Host()
    host.agent_workdir = str(neg_env.team_dir / "agent" / "小美")
    driver = TspAgentDriver(
        host,
        AgentDriverConfig(
            driver_type=DriverType.TSP,
            options={"request_timeout_sec": 190, "workdir": host.agent_workdir},
        ),
    )

    entered = False
    try:
        assert await driver.connect(ExecEnv.HOST) is True

        # —— 能进：gtsp 是静态二进制，无 bash 的镜像照样能拉起
        await containerService.enter_container(1, record.docker_id, driver)
        entered = True
        assert driver.exec_env == ExecEnv.CONTAINER

        # —— 文件工具可用：这是「容器还能用」的证据，不能被 execute_bash 的失败带偏
        write_result = await driver._execute_tsp_tool(
            '{"file_path": "/workspace/self/probe.txt", "content": "hi"}',
            context=roomService.ToolCallContext(agent_id=1, team_id=1, tool_name="write_file"),
        )
        # `write_file` 的成功返回体是 `{"file_path": ..., "written": N}`，**没有 `success` 键**
        # （gtsp v0.4.17 在 alpine / distroless / ubuntu 三镜像实测一致）。
        # 故原写法 `write_result.get("success") is True` 恒为 False，与本用例要验的
        # 「文件工具可用」无关却必红 —— 这是本文件自己的 bug，被 skip 掩盖了很久。
        #
        # 改成两条按**真实契约**写的断言，缺一不可：
        # ① `is not False`：与工具层 `toolRegistry` 的
        #    `tool_succeeded = raw_success is not False` 完全同口径（缺省即成功）。
        #    ⚠️ 不能写成「`error_code` 不存在」——`write_file` 真失败时返回的是
        #    `{"success": False, "code": "server/internal_error", "message": ...}`，
        #    **也没有 `error_code`**（只有缺 bash 那一类才有），那样写失败也会绿。
        # ② `written == 2`：证明真的落盘了，不是「没报错但什么都没干」。
        assert write_result.get("success") is not False, (
            f"无 bash 镜像的文件工具必须可用: {write_result}"
        )
        assert write_result.get("written") == 2, f"文件工具必须真写入: {write_result}"

        # —— execute_bash 报「明确原因 + 出路」（G1 已落地，见 tspDriver._execute_tsp_tool）
        bash_result = await driver._execute_tsp_tool(
            '{"command": "echo hi"}',
            context=roomService.ToolCallContext(agent_id=1, team_id=1, tool_name="execute_bash"),
        )
        assert bash_result.get("success") is False, f"无 bash 时必须明确失败: {bash_result}"

        # G1 已落地：`tspDriver._execute_tsp_tool` 识别「exec: "bash": executable file
        # not found in $PATH」后，在保留协议层 `code` 的同时补出业务层 `error_code`。
        assert bash_result.get("error_code") == containerService.ERROR_CONTAINER_MISSING_BASH, (
            "step1_product.md:45 要求此处给出明确原因；当前返回的是 gtsp 原始错误 "
            f"{bash_result!r}（缺口 G1）。"
        )
        mess = str(bash_result.get("message", "")).lower()
        assert "bash" in mess, "原因必须点名「镜像里没有 bash」，否则模型会去查网络/权限"
        assert any(token in mess for token in ("换", "install", "apt", "apk")), (
            "必须给出路：换镜像，或在容器内装 bash。"
        )
    finally:
        if entered:
            await containerService.exit_to_host(1, driver)
            assert driver.exec_env == ExecEnv.HOST, "退出必回宿主（§5.2）"
        await driver.disconnect()


# ---------------------------------------------------------------------------
# ②b 反向：**bash 在、命令不存在**绝不能报 `container_missing_bash`
# ---------------------------------------------------------------------------


@_DOCKER_E2E_SKIP
async def test_bash_exists_but_command_missing_is_not_reported_as_missing_bash(neg_env):
    """反向用例：`bash: xxx: command not found` 不是「镜像缺 bash」。

    ## 为什么必须有这条

    正向用例（②）只证明「缺 bash 时报了 `container_missing_bash`」。它**不能**证明
    匹配串够窄 —— 把匹配放宽成 `not found` 也能让正向用例变绿，而那时
    「用户把命令写错」会被误报成「镜像缺 bash」，模型会去换镜像，真正该做的是改命令。
    这就是「用错误方式让测试变绿」，本用例是它唯一的防线。

    ## 真实报文（ubuntu:24.04 实测，与缺 bash **不是同一条路径**）

    ```
    {"id":"4","type":"result","result":{"success":false,"stdout":"",
     "stderr":"bash: line 1: this_command_does_not_exist_xyz: command not found\n",
     "exit_code":127}}
    ```

    注意它是 `type:"result"`（**成功调用，命令退出码 127**），压根不抛 `TSPException`，
    所以走不到 `tspDriver.py` 的识别分支。这条用例因此是**双保险**：
    - 现状下它天然绿（路径都进不去）；
    - 但只要有人把识别逻辑下沉到能看见 `result` 分支的层（`tools.py` / 外层
      `except Exception`），或把匹配串放宽成 `not found`，它立刻变红。
    """
    from service.agentService.agentHistoryStore import AgentHistoryStore
    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.tspDriver import TspAgentDriver
    from service.agentService.toolRegistry import AgentToolRegistry

    record = await containerService.create_container(1, image="ubuntu:24.04")
    neg_env.track(record)
    assert record.docker_id is not None

    @dataclass
    class _Host:
        gt_agent: GtAgent = field(
            default_factory=lambda: GtAgent(id=1, team_id=1, name="小美", role_template_id=1)
        )
        system_prompt: str = ""
        agent_workdir: str = ""
        _history: AgentHistoryStore = field(default_factory=lambda: AgentHistoryStore(agent_id=1))
        tool_registry: AgentToolRegistry = field(default_factory=AgentToolRegistry)

        async def execute_pending_tools(self) -> None:
            return None

    host = _Host()
    host.agent_workdir = str(neg_env.team_dir / "agent" / "小美")
    driver = TspAgentDriver(
        host,
        AgentDriverConfig(
            driver_type=DriverType.TSP,
            options={"request_timeout_sec": 190, "workdir": host.agent_workdir},
        ),
    )

    entered = False
    try:
        assert await driver.connect(ExecEnv.HOST) is True
        await containerService.enter_container(1, record.docker_id, driver)
        entered = True
        assert driver.exec_env == ExecEnv.CONTAINER

        # —— 前置自证：这台镜像的 bash 确实在（用 `echo` 证明 execute_bash 正常）
        ok_result = await driver._execute_tsp_tool(
            '{"command": "echo hi"}',
            context=roomService.ToolCallContext(agent_id=1, team_id=1, tool_name="execute_bash"),
        )
        assert ok_result.get("success") is True, f"ubuntu 镜像上 execute_bash 必须可用: {ok_result}"

        # —— 反向：命令不存在
        bad_result = await driver._execute_tsp_tool(
            '{"command": "this_command_does_not_exist_xyz"}',
            context=roomService.ToolCallContext(agent_id=1, team_id=1, tool_name="execute_bash"),
        )
        assert bad_result.get("success") is False, f"命令不存在必须失败: {bad_result}"
        assert bad_result.get("error_code") != containerService.ERROR_CONTAINER_MISSING_BASH, (
            "「命令不存在」被误报成「镜像缺 bash」会让模型去换镜像，"
            f"而真正该做的是改命令: {bad_result!r}"
        )
        # 原文必须能看到是命令名的问题，不是 bash 的问题
        raw = f"{bad_result.get('stderr', '')}{bad_result.get('message', '')}".lower()
        assert "command not found" in raw, f"原文应指向命令不存在: {bad_result!r}"
        assert "executable file not found" not in raw, (
            f"不该出现 exec 层的 bash 缺失报文: {bad_result!r}"
        )
    finally:
        if entered:
            await containerService.exit_to_host(1, driver)
            assert driver.exec_env == ExecEnv.HOST, "退出必回宿主（§5.2）"
        await driver.disconnect()


# ---------------------------------------------------------------------------
# ③ 不依赖 Docker 的常驻护栏：错误分类契约（不受 skip 影响，始终运行）
# ---------------------------------------------------------------------------


def test_mount_denied_markers_are_classified_before_not_found_markers():
    """分类器契约：「挂载被拒」的判定优先于「no such container」。

    这不是 mock docker：`_classify_docker_error` 是纯函数，入参是已经捕获的
    `DockerCliError`（stderr 原文）。本机起不了 Docker Desktop，
    所以「挂载被拒」这条语义**只能在这一层做不依赖环境的固定**；
    真机端到端由上面 skip 的用例负责，两者不重叠。

    为什么值得单独钉：`_MOUNT_DENIED_MARKERS` 与 `no such container` 都是子串匹配，
    真实 stderr 可能同时包含两者（例如先挂载失败、再报容器已被回滚删除）。
    判定顺序一旦反了，模型会收到「容器不存在」而去重建容器 —— 重建仍会撞同一堵墙。
    """
    denied = DockerCliError(
        "docker create failed",
        containerService.ERROR_CONTAINER_CREATE_FAILED,
        stderr=(
            "Error response from daemon: invalid mount config: bind source path "
            "/Volumes/PDATA/foo is not shared from the host"
        ),
    )
    assert (
        container_core._classify_docker_error(
            denied, containerService.ERROR_CONTAINER_CREATE_FAILED
        )
        == containerService.ERROR_CONTAINER_MOUNT_DENIED
    )

    # 同时含两种特征时，必须是「挂载被拒」胜出
    mixed = DockerCliError(
        "docker create failed",
        containerService.ERROR_CONTAINER_CREATE_FAILED,
        stderr="Error response from daemon: mounts denied: no such container: abc123",
    )
    assert (
        container_core._classify_docker_error(
            mixed, containerService.ERROR_CONTAINER_CREATE_FAILED
        )
        == containerService.ERROR_CONTAINER_MOUNT_DENIED
    ), "挂载被拒与 no such container 同时出现时，必须报挂载被拒 —— 否则模型会去重建容器"

    # Docker Desktop 的另一种文案形态
    desktop = DockerCliError(
        "docker create failed",
        containerService.ERROR_CONTAINER_CREATE_FAILED,
        stderr=(
            "docker: Error response from daemon: Mounts denied: \n"
            "The path /Users/x/project is not shared from the host and is not known to Docker."
        ),
    )
    assert (
        container_core._classify_docker_error(
            desktop, containerService.ERROR_CONTAINER_CREATE_FAILED
        )
        == containerService.ERROR_CONTAINER_MOUNT_DENIED
    )

    # 兜底码由调用方决定：同一段 stderr，创建路径报 create_failed，进入路径报 enter_failed
    unknown = DockerCliError("some brand new docker error", "x", stderr="some brand new docker error")
    assert (
        container_core._classify_docker_error(unknown, containerService.ERROR_CONTAINER_CREATE_FAILED)
        == containerService.ERROR_CONTAINER_CREATE_FAILED
    )
    assert (
        container_core._classify_docker_error(unknown, containerService.ERROR_CONTAINER_ENTER_FAILED)
        == containerService.ERROR_CONTAINER_ENTER_FAILED
    ), "兜底码写死会让进入失败报成创建失败，模型据此改参数去换镜像，全是无效动作"

    # CLI 缺失属于环境问题，不该被降级成容器业务错误
    missing_cli = DockerCliError("docker not found", ERROR_DOCKER_CLI_NOT_FOUND, stderr="docker not found")
    assert (
        container_core._classify_docker_error(missing_cli, containerService.ERROR_CONTAINER_CREATE_FAILED)
        == ERROR_DOCKER_CLI_NOT_FOUND
    )
