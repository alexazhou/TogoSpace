"""`containerService.core` 的编排测试。

用**假 docker 可执行文件**（Python 脚本，行为由 JSON 配置驱动）驱动真实子进程，
DB 用真实的 `0017` 迁移 DDL + 临时文件库。这样测的是「编排是否按 §5 的顺序与
补偿语义执行」，而不是我自己的 mock 是否自洽。

每个用例都能断言「到底给 docker 传了什么、以什么顺序、失败后清理了什么」——
这三件事正是 §5.3 补偿流程的验收点。

为什么不用真 Docker：本机是 OrbStack（≠ Docker Desktop，任务 #109 事件 344），
挂载拒绝这类场景在这里不可能真实复现；相关用例在 e2e 层标 skip 并写明前置条件，
绝不在单测里造假的实现或 mock 包装层。
"""

from __future__ import annotations

import json
import sqlite3
import string
import sys
from pathlib import Path

import pytest

import appPaths
from exception import TogoException
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentContainer import GtAgentContainer
from model.dbModel.gtTeam import GtTeam
from service.containerService import core
from service.containerService.dockerCli import DockerCli
from service.ormService import AioSqliteDatabase

_MIGRATION_DIR = Path(__file__).resolve().parents[4] / "assets" / "migrate"

# 只建本模块依赖的两张表（teams / agents），容器表走真实的 0017 迁移。
# 全量跑一遍迁移太慢，且与本模块无关。
_SCHEMA_SQL = """
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

# 假 docker：把每次调用的 argv 追加到 calls.jsonl，再从 behavior.json 里找该
# 子命令的 (stdout, stderr, returncode)。没配的子命令默认成功、无输出。
#
# 用 `string.Template`（`$var`）而不是 `str.format`：脚本体里全是 Docker 的
# `--format {{.Os}}`，`format` 会把 `{{` 当转义吃掉，写起来极易出错。
_FAKE_DOCKER = """#!$python
import json
import sys
from pathlib import Path

calls_path = Path($calls)
config_path = Path($config)

argv = sys.argv[1:]
with calls_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv, ensure_ascii=False) + "\\n")

behavior = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
sub = argv[0] if argv else ""

# 键的匹配优先级：完整 argv 前缀精确 → 前缀通配（以 * 结尾）→ 子命令 → 默认
# 通配是为了 `image inspect --format {{.Os}} <任意镜像>` 这类「末位参数会变」的场景
entry = None
joined = " ".join(argv)
for candidate in (joined, " ".join(argv[:5]), " ".join(argv[:4]), sub):
    if candidate in behavior:
        entry = behavior[candidate]
        break
if entry is None:
    for key, value in behavior.items():
        if key.endswith("*") and joined.startswith(key[:-1]):
            entry = value
            break
entry = entry or {}

sys.stdout.write(entry.get("stdout", ""))
sys.stderr.write(entry.get("stderr", ""))
sys.exit(entry.get("returncode", 0))
"""


class FakeDocker:
    """假 docker 的配置与调用记录封装，测试里直接改行为。"""

    def __init__(self, script: Path, calls: Path, config: Path) -> None:
        self.script = script
        self.calls_path = calls
        self.config_path = config

    def _read(self) -> dict:
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def set(self, key: str, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        """设置某个子命令的返回（key 形如 `create` 或 `image --format {{.Os}}`）。"""
        data = self._read()
        data[key] = {"stdout": stdout, "stderr": stderr, "returncode": returncode}
        self._write(data)

    def set_image_os(self, value: str) -> None:
        self._patch_image_inspect("Os", value)

    def set_image_arch(self, value: str) -> None:
        self._patch_image_inspect("Architecture", value)

    def _patch_image_inspect(self, field: str, value: str) -> None:
        """改 `image inspect --format {{.<field>}}` 的输出。

        假脚本按「前 5 个参数」拼键，而实际调用是
        `image inspect --format {{.Os}} <镜像>`（第 5 个是被查对象）。
        键里带上镜像名占位，与真实调用对齐。
        """
        data = self._read()
        # 用前缀通配键：末位的镜像名/镜像 ID 在不同用例里不同，不能精确匹配。
        # 这里显式拼出 `image inspect --format {{.Os}}*`：不要用 f-string 里套
        # `{{{{...}}}}`，那种写法极易丢掉 field 前的点号（写成 `{{Os}}` 后
        # docker 侧拿到的格式串就错了，而失败会伪装成「架构不支持」）。
        key = "image inspect --format {{." + field + "}}*"
        data[key] = {"stdout": value + "\n", "stderr": "", "returncode": 0}
        self._write(data)

    def calls(self) -> list[list[str]]:
        if not self.calls_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.calls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def subcommands(self) -> list[str]:
        return [call[0] for call in self.calls() if call]

    def calls_of(self, subcommand: str) -> list[list[str]]:
        return [call for call in self.calls() if call and call[0] == subcommand]


@pytest.fixture
async def env(tmp_path, monkeypatch):
    """搭好「临时库 + 假 docker + 真实目录」的运行环境，返回 FakeDocker。"""
    # 1) 数据库：依赖表 + 真实的 0017 迁移
    db_path = tmp_path / "container.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.executescript(
            (_MIGRATION_DIR / "0017_agent_containers.sql").read_text(encoding="utf-8")
        )
        conn.commit()
    finally:
        conn.close()

    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()

    # 2) 工作目录（目录真实存在，因为 create argv 里会带上它们的绝对路径）
    workspace = tmp_path / "ws"
    (workspace / "team-a" / "agent" / "小美").mkdir(parents=True)
    (workspace / "team-a" / "shared").mkdir(parents=True)

    # 3) 假 docker
    calls_path = tmp_path / "calls.jsonl"
    config_path = tmp_path / "behavior.json"
    script = tmp_path / "docker"
    template = string.Template(_FAKE_DOCKER)
    script.write_text(
        template.substitute(
            python=sys.executable,
            calls=repr(str(calls_path)),
            config=repr(str(config_path)),
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    fake = FakeDocker(script=script, calls=calls_path, config=config_path)
    fake.set_image_os("linux")
    fake.set_image_arch("aarch64")
    fake.set("create", stdout="c0ffee1234567890abcdef\n")

    core.reset_cli_for_test(DockerCli(binary_path=str(script)))

    # 4) 让 workspaceUtil 解析到 tmp 目录
    monkeypatch.setattr(
        core.workspaceUtil, "resolve_team_workdir",
        lambda team_name, team_config, workspace_root: str(workspace / team_name),
    )
    monkeypatch.setattr(
        core.workspaceUtil, "resolve_shared_workdir",
        lambda team_workdir: str(Path(team_workdir) / "shared"),
    )
    monkeypatch.setattr(
        core.workspaceUtil, "resolve_agent_workdir",
        lambda team_workdir, agent_name: str(Path(team_workdir) / "agent" / str(agent_name)),
    )
    monkeypatch.setattr(
        core.workspaceUtil, "resolve_workspace_root", lambda _root: str(workspace)
    )

    # 5) 造 Team + Agent
    # 注意用 `aio_save(force_insert=True)` 而不是 `Model.create()`：
    # `create()` 走 peewee 的同步执行，在 AioDatabase（_allow_sync=False）下会抛
    # `AssertionError: sync query is not allowed`。
    await GtTeam(name="team-a", config={}, i18n={}).aio_save(force_insert=True)
    await GtAgent(
        team_id=1, name="小美", role_template_id=1, i18n={"display_name": "小美"}
    ).aio_save(force_insert=True)

    try:
        yield fake
    finally:
        core.reset_cli_for_test(None)
        await db.aio_close()
        unbind_database()


# ---------------------------------------------------------------------------
# 命名与索引选择（纯函数）
# ---------------------------------------------------------------------------


def test_container_name_sanitizes_unicode_but_keeps_ascii_readable():
    """ASCII 名保留可读性；纯中文名回退到 agent_id（Docker 不接受中文容器名）。"""
    assert core.build_container_name(7, "xiao-mei", 1) == "agent-xiao-mei-1"
    assert core.build_container_name(7, "Xiao Mei", 2) == "agent-xiao-mei-2"
    assert core.build_container_name(7, "小美", 1) == "agent-7-1"


def test_container_name_does_not_truncate_long_names():
    """长名不截断：`container_name` 参与部分唯一索引，截断会让两个 Agent 撞名。

    Docker 对容器名长度本身没有限制（它不影响 hostname），所以没有截断的必要。
    """
    long_name = "a" * 100

    name = core.build_container_name(7, long_name, 1)

    assert long_name in name
    assert len(name) > 100


def test_pick_free_index_uses_smallest_available():
    """在 1..3 中取未占用的最小编号（删除后编号应能被复用）。"""
    names = [
        core.build_container_name(7, "xiao-mei", 1),
        core.build_container_name(7, "xiao-mei", 3),
    ]

    assert core.pick_free_index(names, 7, "xiao-mei") == 2


def test_pick_free_index_returns_none_when_full():
    """三个编号全占满时返回 None（由调用方报 `container_limit_reached`）。"""
    names = [core.build_container_name(7, "x", index) for index in (1, 2, 3)]

    assert core.pick_free_index(names, 7, "x") is None


def test_short_id_pattern_rejects_names_and_images():
    """严格 12 位十六进制：容器名 / 镜像名 / 全 ID 都不能当 container_id 用。"""
    assert core.is_short_id("c0ffee123456")
    assert not core.is_short_id("ubuntu:24.04")
    assert not core.is_short_id("agent-x-1")
    assert not core.is_short_id("c0ffee1234567890abcdef")  # 全 ID
    assert not core.is_short_id("C0FFEE123456")  # 大写


# ---------------------------------------------------------------------------
# create argv：每条约束都对应一次真实踩坑，必须逐条钉住
# ---------------------------------------------------------------------------


def _create_argv(**overrides) -> list[str]:
    kwargs = {
        "container_name": "agent-x-1",
        "record_id": 5,
        "image": "ubuntu:24.04",
        "agent_workdir": "/h/a",
        "shared_workdir": "/h/s",
    }
    kwargs.update(overrides)
    return core.build_create_argv(**kwargs)


def test_create_argv_has_required_flags_and_ordering():
    """`-i` 存在、**无 `-t`**、`--entrypoint` 在镜像名之前、含 `--init` 与 `--log-path`。

    这几条是实测结论（任务 #109 事件）：加 `-t` 会分配伪终端改写 stdio 原始流；
    `--entrypoint` 放在镜像名后会被当成容器 CMD，gtsp 根本不会被执行。
    """
    argv = _create_argv()

    assert "-i" in argv
    assert "-t" not in argv and "--tty" not in argv
    assert "--init" in argv
    assert argv.index("--entrypoint") < argv.index("ubuntu:24.04")
    assert "--log-path" in argv
    # 明确不允许的项
    assert "--read-only" not in argv
    assert "--workdir" not in argv
    assert "-w" not in argv


def test_create_argv_mounts_self_rw_and_shared_readonly():
    """个人目录读写、团队共享目录只读（§9 安全要求）。"""
    argv = _create_argv()

    mounts = [argv[index + 1] for index, item in enumerate(argv) if item == "--mount"]

    assert "type=bind,src=/h/a,dst=/workspace/self" in mounts
    assert "type=bind,src=/h/s,dst=/workspace/team_shared,readonly" in mounts


def test_create_argv_carries_record_id_label():
    """label 值必须等于 record_id：按名清理预记录时靠它做归属校验。"""
    argv = _create_argv(record_id=42)

    label_index = argv.index("--label")
    assert argv[label_index + 1] == f"{core.CONTAINER_LABEL_RECORD_ID}=42"


def test_create_argv_passes_gtsp_stdio_args_after_image():
    """镜像名之后是容器 CMD：gtsp 的启动参数（`--mode stdio` + `--log-path`）。"""
    argv = _create_argv()

    image_index = argv.index("ubuntu:24.04")
    tail = argv[image_index + 1:]

    assert tail[:2] == ["--mode", "stdio"]
    assert tail[tail.index("--log-path") + 1] == core.CONTAINER_LOG_DIR


def test_create_argv_does_not_leak_api_keys_or_mount_docker_socket():
    """安全红线的回归锁：只传 HOME，不挂 docker.sock、不 privileged（§9）。"""
    argv = _create_argv()

    env_values = [argv[index + 1] for index, item in enumerate(argv) if item == "--env"]
    joined = " ".join(argv)

    assert env_values == ["HOME=/tmp"]
    assert "docker.sock" not in joined
    assert "--privileged" not in argv
    assert "no-new-privileges" in joined


def test_container_start_and_exec_argv_shapes():
    """形态 A `start -a -i` / 形态 B `exec -i`（§3.2 两种启动方式，不能互换）。"""
    assert core.build_container_start_argv("c0ffee123456") == [
        "start", "-a", "-i", "c0ffee123456",
    ]
    exec_argv = core.build_container_exec_argv("c0ffee123456")
    assert exec_argv[:4] == ["exec", "-i", "c0ffee123456", core.CONTAINER_GTSP_PATH]
    assert exec_argv[4:6] == ["--mode", "stdio"]
    # 形态 B 绝不能带 -a / start：外部容器主进程不能被动
    assert "-a" not in exec_argv
    assert "start" not in exec_argv


# ---------------------------------------------------------------------------
# create_container 编排：预记录 → create → 回填 → 注入
# ---------------------------------------------------------------------------


async def test_create_container_follows_pre_record_then_create_then_inject(env):
    """§5.3 顺序：先插 `docker_id=NULL` 预记录 → create → 回填 → cp 注入。

    顺序本身就是设计要点：先落预记录才能占住配额与名称，防并发绕过上限。
    """
    record = await core.create_container(1)

    assert record.docker_id == "c0ffee123456"
    assert record.container_name == "agent-1-1"  # 中文名 → 回退到 agent_id
    assert record.image == "ubuntu:24.04"

    subcommands = env.subcommands()
    assert "create" in subcommands
    assert "cp" in subcommands
    assert subcommands.index("create") < subcommands.index("cp")


async def test_create_container_injects_the_matching_arch_binary(env):
    """注入的必须是**目标架构**的 Linux 产物，注入路径恒为 `/tmp/gtsp`。"""
    await core.create_container(1)

    cp_calls = env.calls_of("cp")
    assert len(cp_calls) == 1
    source, target = cp_calls[0][1], cp_calls[0][2]

    assert source == appPaths.get_gtsp_linux_binary_path("arm64")
    assert target == "c0ffee123456:/tmp/gtsp"


async def test_create_container_uses_default_image_when_blank(env):
    """`image=None` 与空白串都走默认镜像（不把空白传给 docker）。"""
    record = await core.create_container(1, image="   ")

    assert record.image == core.DEFAULT_CONTAINER_IMAGE


async def test_create_container_hits_limit_after_three(env):
    """配额上限 3：第 4 个报 `container_limit_reached`，且不产生新记录、不调 docker。"""
    for index in (1, 2, 3):
        await core.gtAgentContainerManager.create(
            agent_id=1,
            container_name=core.build_container_name(1, "小美", index),
            image="ubuntu:24.04",
        )
    before = len(env.calls())

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_LIMIT_REACHED
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 3
    # 超限时连 docker 都不该调（不该先探测镜像再报错）
    assert len(env.calls()) == before


async def test_create_container_rejects_non_linux_image_before_touching_db(env):
    """非 Linux 镜像必须在**落库前**被拒：不留任何记录（§11 缺失均不落库）。"""
    env.set_image_os("windows")

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_IMAGE_NOT_LINUX
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0


async def test_create_container_rejects_unsupported_arch_before_touching_db(env):
    """架构不支持的镜像同样不落库（`container_arch_unsupported`）。"""
    env.set_image_arch("s390x")

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_ARCH_UNSUPPORTED
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0


async def test_create_failure_removes_pre_record(env):
    """`docker create` 非 0 退出 ⇒ 物理删除预记录，不留占配额的残留（§5.3 第 4 步）。"""
    env.set("create", stderr="Error response from daemon: boom", returncode=1)

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_CREATE_FAILED
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0


async def test_inject_failure_removes_container_and_pre_record(env):
    """注入失败 ⇒ 删容器 + 物理删除预记录，返回 `container_not_executable`（§5.3 第 6 步）。

    这条同时钉住「补偿删除按 docker_id」：断言 rm 的参数是 12 位短 ID，
    而不是容器名——按名删有可能命中宿主上的外部同名容器（P-1 风险）。
    """
    env.set("cp", stderr="Error response from daemon: not found", returncode=1)

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOT_EXECUTABLE
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0

    rm_calls = env.calls_of("rm")
    assert rm_calls == [["rm", "-f", "c0ffee123456"]]


async def test_create_short_id_from_full_id_prefix(env):
    """`docker create` 输出完整 ID，库里只存前 12 位（§2.1 统一使用短 ID）。"""
    env.set("create", stdout="0123456789abcdef0123456789abcdef\n")

    record = await core.create_container(1)

    assert record.docker_id == "0123456789ab"
    assert len(record.docker_id) == 12


async def test_create_rejects_abnormal_create_output(env):
    """`docker create` 输出不足 12 位时按失败处理并清理，不写脏短 ID 进库。"""
    env.set("create", stdout="abc\n")

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_CREATE_FAILED
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0


async def test_docker_absent_reports_cli_not_found_and_leaves_no_record(env, monkeypatch):
    """docker 不存在时透传 `docker_cli_not_found`，且不产生任何 DB 记录。"""
    core.reset_cli_for_test(None)
    from service.containerService import dockerCli as docker_cli_module

    def _boom():
        raise docker_cli_module.DockerCliError(
            "未找到 docker 命令", docker_cli_module.ERROR_DOCKER_CLI_NOT_FOUND
        )

    monkeypatch.setattr(core, "get_cli", _boom)

    with pytest.raises(docker_cli_module.DockerCliError) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_DOCKER_CLI_NOT_FOUND
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0


async def test_create_reports_name_conflict_as_togo_exception(env):
    """容器名被其他 Team 的同名 Agent 占用 ⇒ `container_name_conflict`，不是裸 IntegrityError（#111 裁定 6）。

    命名规则 `agent-<name>-<index>` 不带 team 前缀，而 `container_name` 在 `deleted=0`
    范围内是**全局**唯一，所以两个 Team 各有「小美」时，第二个人创建容器必然撞名。
    裸 `IntegrityError` 的 `error_code` 是 None，模型只会看到「数据库错误」，无从知道
    该怎么改；包成明确错误码后它至少知道「这个名字被别人占了」。
    """
    # 另一个 Agent（模拟另一个 Team 的同名 Agent）先占住这个名字：
    # 中文名会回退成 `agent-<agent_id>-<index>`，所以 agent 1 的名字是 `agent-1-1`。
    await core.gtAgentContainerManager.create(
        agent_id=2, container_name="agent-1-1", image="ubuntu:24.04"
    )

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NAME_CONFLICT
    # 不能是 peewee 的裸 IntegrityError（那类异常的 error_code 为 None）
    assert not isinstance(excinfo.value.__cause__, type(None))
    # 撞名时不该留下自己的记录，也不该调 docker create
    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 0
    assert env.calls_of("create") == []


async def test_pre_record_is_visible_in_list_and_counts_toward_quota(env):
    """创建中的预记录（`docker_id=NULL`）计入配额并出现在列表里（§2.1）。"""
    await core.gtAgentContainerManager.create(
        agent_id=1, container_name="agent-1-1", image="ubuntu:24.04"
    )

    assert await core.gtAgentContainerManager.count_active_by_agent(1) == 1
    records = await core.gtAgentContainerManager.list_active_by_agent(1)
    assert [(row.container_name, row.docker_id) for row in records] == [("agent-1-1", None)]
