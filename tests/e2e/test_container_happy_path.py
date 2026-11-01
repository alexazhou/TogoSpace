"""第 9 步：真实 Docker e2e（happy path）。

**这组用例会真的创建 / 启动 / 删除 Docker 容器**，因此默认**不跑**（整组 skip），
必须显式开闸：

    TOGOSPACE_TEST_DOCKER=1 python -m pytest tests/e2e/test_container_happy_path.py -q

## 为什么不塞进单测

单测用假 docker 脚本验的是「命令拼得对不对」；这里验的是「拼出来的命令真实
Docker 认不认」。只有真 daemon 才会暴露的问题：

- `docker create` 的 `--mount` 源路径是否被 File sharing 允许（§3.2 / §12）
- `docker cp` 后 `/tmp/gtsp` 是否真的可执行（权限由源文件保证，不做容器内 chmod）
- `docker start -a -i` attach 后 stdio 是否通（TSP 握手能否完成）
- 关 stdin 后自建容器是否真的停回 `exited`

## 环境硬约束（Operator 指示）

- **禁止**在仓库目录启动常驻开发服务器。本文件不起 HTTP 服务、只调 service 层，
  因此不受该约束；代价是**不覆盖** controller / WS 层（那部分由单测 + #113 覆盖）。
- 每个用例自建临时 workspace，容器在 fixture 收尾时按 `container_id` 兜底 `rm -f`。
- 镜像用 `ubuntu:24.04`（glibc 系，当前唯一承诺兼容的发行版系列，事件 343）。

## 口径（事件 343 / 344）

- gtsp 产物用仓库现有的，**不做静态化替换**。
- alpine / distroless 已知不支持、`container_mount_denied` 在本机 OrbStack 不可复现
  → 负向用例标 skip，见 `test_container_negative_cases.py`（李哥负责）。
- 本机环境：OrbStack 29.4.0 / aarch64。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from constants import ContainerState, ExecEnv
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from service import agentService, containerService
from service.containerService import core as container_core
from service.containerService.dockerCli import DockerCli, DockerCliError

DOCKER_E2E_ENABLED = os.environ.get("TOGOSPACE_TEST_DOCKER") == "1"

_DOCKER_SKIP = pytest.mark.skipif(
    not DOCKER_E2E_ENABLED,
    reason=(
        "真实 Docker e2e：会创建/启动/删除真实容器。"
        "需显式开闸 TOGOSPACE_TEST_DOCKER=1 才会运行"
    ),
)

# 注意：这里**不用 `pytestmark`**。文件末尾的 schema 防漂移用例不碰 Docker，
# 必须始终运行——否则「ORM 加字段导致 fixture 建表缺列」这类问题要等到有人记得
# 开闸跑 e2e 才会暴露。

_MIGRATION_DIR = Path(__file__).resolve().parents[2] / "assets" / "migrate"

# 容器服务真正依赖的两张业务表。字段与 `model/dbModel` 的一致性由文件末尾
# `test_min_schema_covers_required_model_columns` 兜住——那份断言是防漂移的
# 唯一保险，不要因为「e2e 反正会报错」就删掉它。
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

_E2E_IMAGE = "ubuntu:24.04"


@dataclass
class E2EEnv:
    """用例共享的环境句柄；`created` 用于兜底清理真实容器。"""

    cli: DockerCli
    workspace: Path
    team_dir: Path
    created: list[str] = field(default_factory=list)

    def track(self, record) -> None:
        if record.container_id:
            self.created.append(record.container_id)


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
async def e2e_env(tmp_path, monkeypatch, real_docker):
    """最小运行环境：临时库 + 临时 workspace + 真实 docker。"""
    from model.dbModel.base import bind_database, unbind_database
    from service.ormService import AioSqliteDatabase

    db_path = tmp_path / "e2e.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # 只建容器模块依赖的两张业务表；容器表走真实的 0017 迁移。
        # 这里**不从单测文件 import schema**：跨测试文件 import 会把别人的类型问题
        # 带进本文件的静态检查，且让 e2e 依赖单测内部私有常量。
        # 漂移风险由 `test_min_schema_matches_model_fields` 兜住（见文件末尾）。
        conn.executescript(_MIN_SCHEMA_SQL)
        conn.executescript((_MIGRATION_DIR / "0017_agent_containers.sql").read_text("utf-8"))
        conn.commit()
    finally:
        conn.close()

    database = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(database)
    await database.aio_connect()

    # 清空 agent 缓存：`gtAgentManager` 是进程级 `CacheStore`（按 `agent.id` 缓存，
    # 命中即跳过 DB 查询）。同进程前序测试若把「id=1 → alice」塞进缓存，这里就会拿到
    # 别人的 agent 名，容器名变 `agent-alice-1`、bind mount 指向不存在的
    # `<team>/agent/alice`，docker create 报 `bind source path does not exist`。
    # 表现为「单跑绿、与其他测试一起跑红」，与容器逻辑无关。
    from dal.db import gtAgentManager

    gtAgentManager.clear_agent_cache()

    # workspace：容器创建时把这两个目录以绝对路径 bind mount 进去
    workspace = tmp_path / "ws"
    team_dir = workspace / "e2e-team"
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

    await GtTeam(name="e2e-team", config={}, i18n={}).aio_save(force_insert=True)
    await GtAgent(
        team_id=1, name="小美", role_template_id=1, i18n={"display_name": "小美"}
    ).aio_save(force_insert=True)

    env = E2EEnv(cli=real_docker, workspace=workspace, team_dir=team_dir)
    try:
        yield env
    finally:
        # 兜底清理：按 container_id 删，绝不用名字（容器名跨 Team 可能重名）
        for container_id in env.created:
            try:
                await real_docker.run(["rm", "-f", container_id], timeout_sec=60)
            except Exception:  # noqa: BLE001 - 清理失败不能盖住用例结果
                pass
        await database.aio_close()
        unbind_database()
        # 别让本用例临时库里的 agent 1 留在进程缓存里污染后续测试
        from dal.db import gtAgentManager

        gtAgentManager.clear_agent_cache()


async def _create(env: E2EEnv):
    """创建一个真实容器并登记清理，返回 DB 记录。"""
    record = await containerService.create_container(1, image=_E2E_IMAGE)
    env.track(record)
    return record


async def _inspect(env: E2EEnv, container_id: str):
    return await container_core._inspect_container(env.cli, container_id)


# ---------------------------------------------------------------------------
# create：真 daemon 认不认这份 argv
# ---------------------------------------------------------------------------


@_DOCKER_SKIP
async def test_create_container_really_creates_docker_container(e2e_env):
    """`create_container` 真的建出容器，且 DB 回填 12 位短 ID、不自动启动。"""
    record = await _create(e2e_env)

    assert containerService.is_short_id(record.container_id)
    inspect = await _inspect(e2e_env, record.container_id)
    assert isinstance(inspect, dict), "创建后容器应立刻能在 daemon 上查到"

    # 只创建、不切环境（§6.2 第 1 条）：容器处于未运行状态
    assert inspect["State"]["Status"] in {"created", "exited"}


@_DOCKER_SKIP
async def test_created_container_honors_argv_contract(e2e_env):
    """真 daemon 上核对 argv 契约：无 TTY、entrypoint 是注入的 gtsp、label 与挂载都在。

    单测断言的是 argv 列表；这里断言的是 **Docker 按这些参数落到容器上的结果**。
    """
    record = await _create(e2e_env)

    inspect = await _inspect(e2e_env, record.container_id)
    assert isinstance(inspect, dict)

    # 绝不能有 TTY：伪终端会改写 gtsp 的 stdio 原始流
    assert inspect["Config"]["Tty"] is False
    assert inspect["Config"]["OpenStdin"] is True

    # --entrypoint 指向注入路径
    assert inspect["Path"] == containerService.CONTAINER_GTSP_PATH

    # label = record_id，按名清理预记录时靠它做归属校验（P-1 防线）
    labels = inspect["Config"]["Labels"] or {}
    assert labels.get(containerService.CONTAINER_LABEL_RECORD_ID) == str(record.id)

    # 两个 bind mount 都在，共享目录只读
    mounts = {item["Destination"]: item for item in inspect["Mounts"]}
    assert containerService.CONTAINER_SELF_MOUNT_PATH in mounts
    assert containerService.CONTAINER_SHARED_MOUNT_PATH in mounts
    assert mounts[containerService.CONTAINER_SELF_MOUNT_PATH]["RW"] is True
    assert mounts[containerService.CONTAINER_SHARED_MOUNT_PATH]["RW"] is False


@_DOCKER_SKIP
async def test_gtsp_is_injected_with_executable_permission(e2e_env):
    """`docker cp` 注入后 `/tmp/gtsp` 可执行。

    这条是 glibc 口径下的最低要求：文件在、且权限 0755。把容器里的文件取回宿主再查
    权限位，等价于在容器内 `stat`（created 状态无法 exec，所以不能直接进容器看）。
    """
    record = await _create(e2e_env)

    local_copy = e2e_env.workspace / f"gtsp-check-{uuid.uuid4().hex}"
    try:
        await e2e_env.cli.run(
            ["cp", f"{record.container_id}:{containerService.CONTAINER_GTSP_PATH}", str(local_copy)],
            timeout_sec=60,
        )
        assert local_copy.is_file(), "注入的 gtsp 应能在容器内取到"
        assert os.access(local_copy, os.X_OK), "docker cp 应原样保留 0755（不做容器内 chmod）"
        # 真实产物必须是 ELF：取回后头 4 字节为 \x7fELF
        assert local_copy.read_bytes()[:4] == b"\x7fELF"
    finally:
        local_copy.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# list / delete
# ---------------------------------------------------------------------------


@_DOCKER_SKIP
async def test_list_reports_real_docker_state(e2e_env):
    """列表状态是 Docker 原值、`is_entered` 正确、工具契约键名正确（§8 / §6.2）。"""
    record = await _create(e2e_env)

    infos = await containerService.list_agent_containers(1)

    assert len(infos) == 1
    assert infos[0].record_id == record.id
    assert infos[0].container_id == record.container_id
    assert infos[0].state in {ContainerState.CREATED, ContainerState.EXITED}, (
        "状态必须是 Docker 原值，不归一化"
    )
    assert infos[0].is_entered is False

    as_tool = infos[0].to_tool_dict()
    assert as_tool["container_id"] == record.container_id
    assert "agent_id" not in as_tool, "工具契约里不该出现内部字段 agent_id"


@_DOCKER_SKIP
async def test_delete_container_removes_real_docker_container(e2e_env):
    """`delete_container` 真的删掉容器，并软删除记录（不再出现在活跃列表）。"""
    record = await _create(e2e_env)
    container_id = record.container_id
    assert container_id is not None

    await containerService.delete_container(1, container_id=container_id)

    # daemon 上确实没了
    assert await _inspect(e2e_env, container_id) is None
    # 活跃列表里也没了
    assert await containerService.list_agent_containers(1) == []


@_DOCKER_SKIP
async def test_container_name_uses_record_id(e2e_env):
    """容器名以自增记录 ID 结尾：删除后新建不会复用旧名（ID 持续递增）。"""
    first = await _create(e2e_env)
    first_name = first.container_name
    await containerService.delete_container(1, container_id=first.container_id)
    e2e_env.created.remove(first.container_id)

    second = await _create(e2e_env)

    assert first_name.endswith(f"-{first.id}")
    assert second.container_name.endswith(f"-{second.id}")
    assert second.container_name != first_name


@_DOCKER_SKIP
async def test_quota_is_enforced_against_real_docker(e2e_env):
    """配额上限在真实 daemon 上同样生效：到上限后拒绝且不产生残留容器。"""
    limit = containerService.MAX_CONTAINERS_PER_AGENT
    for _ in range(limit):
        await _create(e2e_env)

    with pytest.raises(TogoException) as excinfo:
        await containerService.create_container(1, image=_E2E_IMAGE)

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_LIMIT_REACHED
    # 超限时不该产生任何 DB 记录
    assert len(await containerService.list_agent_containers(1)) == limit


# ---------------------------------------------------------------------------
# 进入 / 退出：真实 TSP 握手（容器能否真的当执行环境用）
# ---------------------------------------------------------------------------


@_DOCKER_SKIP
async def test_enter_then_exit_container_with_real_handshake(e2e_env, monkeypatch):
    """真实容器里拉起 gtsp 完成 TSP 握手，再退回宿主。

    这是「容器能当执行环境用」的唯一硬证据：glibc 兼容性、注入权限、stdio 连通性
    任一有问题，握手都会失败。用例失败不阻塞交付（按 Operator 口径记 issue），
    但必须如实报出失败原因。
    """
    from constants import DriverType
    from service.agentService.agentHistoryStore import AgentHistoryStore
    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.tspDriver import TspAgentDriver
    from service.agentService.toolRegistry import AgentToolRegistry

    record = await _create(e2e_env)
    assert record.container_id is not None

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
    host.agent_workdir = str(e2e_env.team_dir / "agent" / "小美")
    driver = TspAgentDriver(
        host,
        AgentDriverConfig(
            driver_type=DriverType.TSP,
            options={
                "request_timeout_sec": 190,
                "workdir": host.agent_workdir,
            },
        ),
    )

    # 这套 e2e 直接构造 Driver 验证真实握手；把 Driver 挂到真实 Agent 上并注册，
    # 环境切换仍走 Agent 的方法。
    from service.agentService.agent import Agent

    agent = Agent(
        gt_agent=GtAgent(id=1, team_id=1, name="小美", role_template_id=1),
        system_prompt="",
    )
    agent.task_consumer._turn_runner.driver = driver
    monkeypatch.setattr(agentService.core, "_agents", {1: agent})

    entered = False
    try:
        # 先连宿主，保证 driver 有可用的初始连接
        assert await driver.connect(ExecEnv.HOST) is True

        await agentService.enter_container(1, record.container_id)
        entered = True

        assert driver.exec_env == ExecEnv.CONTAINER
        assert driver.current_container_id == record.container_id
    finally:
        if entered:
            await agentService.exit_to_host(1)
            assert driver.exec_env == ExecEnv.HOST, "退出必回宿主（§5.2）"
            assert driver.current_container_id is None
        await driver.disconnect()

    # 关 stdin 后自建容器应停回 exited（gtsp os.Exit(0)），而不是仍在 running
    inspect = await _inspect(e2e_env, record.container_id)
    assert isinstance(inspect, dict)
    assert inspect["State"]["Status"] in {"exited", "created"}, (
        "退出后容器应停止；若仍 running 说明 gtsp 没有随 stdin 关闭退出"
    )


# ---------------------------------------------------------------------------
# 防漂移：最小 schema 必须覆盖模型实际用到的列
# ---------------------------------------------------------------------------


def test_min_schema_covers_required_model_columns():
    """本文件的最小建表必须覆盖 `GtTeam` / `GtAgent` 用到的列。

    这条不是"锦上添花"：我第一版手写 schema 漏了 `teams.deleted`，整个 fixture 直接
    `OperationalError: table teams has no column named deleted`。ORM 新增字段时这条会
    在**不开 Docker 的前提下**立刻变红，比跑到 e2e 才发现便宜得多。
    """
    import sqlite3 as _sqlite3

    from model.dbModel.gtAgent import GtAgent
    from model.dbModel.gtTeam import GtTeam

    conn = _sqlite3.connect(":memory:")
    try:
        conn.executescript(_MIN_SCHEMA_SQL)
        for model in (GtTeam, GtAgent):
            table = model._meta.table_name
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            # 模型声明的字段名（排除反向关系等非列属性）
            expected = {
                name for name, fld in model._meta.fields.items() if not getattr(fld, "backref", None)
            }
            missing = expected - actual
            assert not missing, f"{table} 的最小 schema 缺少列: {sorted(missing)}"
    finally:
        conn.close()
