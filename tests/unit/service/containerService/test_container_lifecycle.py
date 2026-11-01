"""`containerService` 的容器查询与删除语义测试（`get_container_target` / `list` / `delete`）。

这部分是 P-1 风险（误删外部同名容器）的主要防线，因此断言重点不是「返回了什么」，
而是「有没有对不属于自己的容器执行破坏性操作」。
"""

from __future__ import annotations

import json

import pytest

from exception import TogoException
from model.coreModel import CONTAINER_STATE_MISSING, CONTAINER_STATE_UNKNOWN
from service.containerService import core

# 复用 create 测试里的环境夹具（假 docker + 真实迁移 DDL + 临时库）
from .test_container_core import env as container_env  # noqa: F401


def _inspect_json(
    *, status: str = "created", image: str = "sha256:abc", labels: dict | None = None,
    name: str | None = None,
) -> str:
    """构造 `docker inspect --format {{json .}}` 的输出。"""
    data = {
        "State": {"Status": status},
        "Image": image,
        "Config": {"Labels": labels or {}},
    }
    if name is not None:
        data["Name"] = name
    return json.dumps(data)


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


async def _seed_record(agent_id: int = 1, container_name: str = "agent-1-1", docker_id: str | None = None):
    """直接往库里插一条记录（跳过 docker），返回对象。"""
    record = await core.gtAgentContainerManager.create(
        agent_id=agent_id, container_name=container_name, image="ubuntu:24.04"
    )
    if docker_id:
        await core.gtAgentContainerManager.set_docker_id(record, docker_id)
    return record


# ---------------------------------------------------------------------------
# get_container_target
# ---------------------------------------------------------------------------


async def test_get_target_rejects_names_and_images_before_calling_docker(fake):
    """非 12 位十六进制一律拒绝，且**不调 docker**。

    这是防止「Agent 把镜像名当容器 ID 传进来」的第一道闸；先校验再调 docker
    也避免了无谓的 daemon 往返。
    """
    for bad in ("ubuntu:24.04", "agent-x-1", "abc", "C0FFEE123456", "c0ffee1234567890"):
        with pytest.raises(TogoException) as excinfo:
            await core.get_container_target(1, bad)
        assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS

    assert fake.calls() == []


async def test_get_target_returns_managed_when_record_matches_agent(fake):
    """`agent_id + docker_id` 命中即自建容器（`is_managed=True`），arch 从镜像取。"""
    await _seed_record(docker_id="c0ffee123456")
    fake.set(
        "inspect",
        stdout=_inspect_json(status="created", image="sha256:abc"),
    )

    target = await core.get_container_target(1, "c0ffee123456")

    assert target.is_managed is True
    assert target.docker_id == "c0ffee123456"
    assert target.arch == "arm64"


async def test_get_target_fills_container_name_for_managed(fake):
    """自建容器的 `container_name` 取自 DB 记录（活动记录 §8 要显示这个名字）。

    这里刻意让 inspect 返回的空缺名字和记录里的名字不同（不返回 Name 字段）：
    自建容器是我们自己命名的，权威来源是记录而不是 `docker inspect`。
    """
    await _seed_record(docker_id="c0ffee123456", container_name="agent-xiao-mei-2")
    fake.set("inspect", stdout=_inspect_json(status="exited", image="sha256:abc"))

    target = await core.get_container_target(1, "c0ffee123456")

    assert target.container_name == "agent-xiao-mei-2"


async def test_get_target_strips_leading_slash_from_external_container_name(fake):
    """外部容器的名字取自 inspect 的 `Name`，并去掉 Docker 加的前导 `/`。

    不去掉会让活动记录里显示 `/my-container`，前端拿去做匹配/展示都会别扭。
    """
    fake.set("inspect", stdout=_inspect_json(status="running", name="/dev-box"))

    target = await core.get_container_target(1, "deadbeef0002")

    assert target.is_managed is False
    assert target.container_name == "dev-box"


async def test_get_target_tolerates_missing_name_for_external_container(fake):
    """`Name` 缺失不能影响连接：它只用于展示，取不到就 None。

    若这里抛错，一个字段缺失就会让「进入容器」整体失败——展示字段不该有这种权力。
    """
    fake.set("inspect", stdout=_inspect_json(status="running"))

    target = await core.get_container_target(1, "deadbeef0002")

    assert target.container_name is None
    assert target.docker_id == "deadbeef0002"


async def test_get_target_does_not_treat_other_agents_record_as_managed(fake):
    """**别人的记录不算自己的**：即使 docker_id 命中，agent 不匹配就是外部容器。

    这条是隔离性的核心：若这里返回 `is_managed=True`，后续 run 中「重连发现容器
    不存在就软删除记录」会去删别人的记录。
    """
    await _seed_record(agent_id=2, container_name="agent-2-1", docker_id="c0ffee123456")
    fake.set("inspect", stdout=_inspect_json(status="running"))

    target = await core.get_container_target(1, "c0ffee123456")

    assert target.is_managed is False


async def test_get_target_reports_container_lost_when_record_exists_but_container_gone(fake):
    """库里有记录但容器没了 ⇒ `container_lost`，且**不在查询路径软删除**（§5.1）。

    软删除只发生在「运行中重连」路径（第 4 步），首次校验只报错。
    """
    record = await _seed_record(docker_id="c0ffee123456")
    fake.set("inspect", stderr="Error: No such container: c0ffee123456", returncode=1)

    with pytest.raises(TogoException) as excinfo:
        await core.get_container_target(1, "c0ffee123456")

    assert excinfo.value.error_code == "container_lost"
    # 记录必须还在（没被悄悄删掉）
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is not None


async def test_get_target_reports_not_found_for_unknown_external_container(fake):
    """既不是自建记录、容器也不存在 ⇒ `container_not_found`。"""
    fake.set("inspect", stderr="Error: No such container: deadbeef0001", returncode=1)

    with pytest.raises(TogoException) as excinfo:
        await core.get_container_target(1, "deadbeef0001")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOT_FOUND


async def test_get_target_uses_image_inspect_for_arch_not_container_platform(fake):
    """架构必须由 `.Image` → `docker image inspect` 取得。

    容器 inspect 的 `.Platform` 只有 OS，没有架构——直接读容器 inspect 会
    永远拿不到 arch，注入的二进制就可能是错的架构（在容器里直接 exec format error）。
    """
    await _seed_record(docker_id="c0ffee123456")
    fake.set("inspect", stdout=_inspect_json(image="sha256:deadbeef"))
    fake.set_image_arch("x86_64")

    target = await core.get_container_target(1, "c0ffee123456")

    assert target.arch == "amd64"
    # 断言确实用镜像 ID 查过架构
    image_calls = fake.calls_of("image")
    assert any("sha256:deadbeef" in call for call in image_calls)


async def test_get_target_reports_unsupported_arch(fake):
    """镜像架构不是 amd64 / arm64 时报 `container_arch_unsupported`（不猜、不硬塞）。"""
    await _seed_record(docker_id="c0ffee123456")
    fake.set("inspect", stdout=_inspect_json())
    fake.set_image_arch("s390x")

    with pytest.raises(TogoException) as excinfo:
        await core.get_container_target(1, "c0ffee123456")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_ARCH_UNSUPPORTED


async def test_get_target_propagates_daemon_unavailable_as_is(fake):
    """inspect 因 daemon 不可用失败 ⇒ 透传 `docker_daemon_unavailable`，不当成容器丢失。"""
    await _seed_record(docker_id="c0ffee123456")
    fake.set(
        "inspect",
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
        returncode=1,
    )

    with pytest.raises(TogoException) as excinfo:
        await core.get_container_target(1, "c0ffee123456")

    assert excinfo.value.error_code == core.ERROR_DOCKER_DAEMON_UNAVAILABLE
    # 关键：不能报 container_lost（那会让上层去软删除记录）
    assert excinfo.value.error_code != "container_lost"


# ---------------------------------------------------------------------------
# list_agent_containers：识别「同名外部容器」不算自己的
# ---------------------------------------------------------------------------


async def test_list_includes_pre_record_with_null_docker_id(fake):
    """预记录必须出现在列表里，`docker_id` 为 None，状态按容器名查得。

    注意：预记录的状态查询**必须**带匹配的 label，否则会被判成 `missing`
    （见 `test_list_marks_missing_when_same_name_container_has_foreign_label`）。
    """
    record = await _seed_record(container_name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(
            status="created", labels={core.CONTAINER_LABEL_RECORD_ID: str(record.id)}
        ),
    )

    infos = await core.list_agent_containers(1)

    assert len(infos) == 1
    assert infos[0].record_id == record.id
    assert infos[0].docker_id is None
    assert infos[0].container_name == "agent-1-1"
    assert infos[0].state == "created"


async def test_list_marks_missing_when_same_name_container_has_foreign_label(fake):
    """**同名但 label 不匹配 ⇒ `missing`**：那个容器不是我们创建的（P-1 关键防线）。

    若不校验 label，宿主上一个恰好叫 `agent-1-1` 的外部容器会被显示成「我们的容器」，
    Agent 随后 delete 就会把它 `rm -f` 掉。
    """
    await _seed_record(container_name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(status="running", labels={"com.togospace.container_record_id": "999"}),
    )

    infos = await core.list_agent_containers(1)

    assert infos[0].state == CONTAINER_STATE_MISSING


async def test_list_accepts_matching_label_for_pre_record(fake):
    """label 匹配 ⇒ 正常返回真实状态（证明上面的用例不是「永远 missing」）。"""
    record = await _seed_record(container_name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(
            status="exited",
            labels={core.CONTAINER_LABEL_RECORD_ID: str(record.id)},
        ),
    )

    infos = await core.list_agent_containers(1)

    assert infos[0].state == "exited"


async def test_list_uses_docker_id_not_name_for_backfilled_records(fake):
    """已回填的记录按 `docker_id` inspect（不看名字，也就不需要 label 校验）。"""
    await _seed_record(container_name="agent-1-1", docker_id="c0ffee123456")
    fake.set("inspect", stdout=_inspect_json(status="exited"))

    await core.list_agent_containers(1)

    inspect_calls = fake.calls_of("inspect")
    assert inspect_calls and inspect_calls[0][-1] == "c0ffee123456"


async def test_list_returns_unknown_when_inspect_fails(fake):
    """inspect 失败 ⇒ `unknown`，**不能**报 missing（否则前端会以为容器没了）。"""
    await _seed_record(docker_id="c0ffee123456")
    fake.set("inspect", stderr="Error response from daemon: timeout", returncode=1)

    infos = await core.list_agent_containers(1)

    assert infos[0].state == CONTAINER_STATE_UNKNOWN


async def test_list_marks_unknown_for_every_record_when_daemon_down(fake):
    """daemon 不可用时不逐条 inspect，全部 `unknown`，且仍返回记录（不能空列表）。"""
    await _seed_record(container_name="agent-1-1")
    await _seed_record(container_name="agent-1-2", docker_id="c0ffee123456")
    fake.set("version", stderr="Cannot connect to the Docker daemon.", returncode=1)

    infos = await core.list_agent_containers(1)

    assert [info.state for info in infos] == [CONTAINER_STATE_UNKNOWN, CONTAINER_STATE_UNKNOWN]


async def test_list_marks_unknown_when_docker_cli_missing(fake, monkeypatch):
    """**没装 Docker CLI 也不能让列表整体失败**（P-5）：与「daemon 没起」表现一致。

    `get_cli()` 是「找不到 docker 可执行文件」的唯一抛出点，必须落在可用性判断**之内**。
    一旦被提到判断之外，这条用例就会拿到 `docker_cli_not_found` 异常——而另一个环境的
    用户只是拿到列表 + 全 `unknown`。两条路径分叉，前端就得处理两种"没 docker"。
    """
    await _seed_record(container_name="agent-1-1")
    await _seed_record(container_name="agent-1-2", docker_id="c0ffee123456")

    from service.containerService import dockerCli as docker_cli_module

    def _boom():
        raise docker_cli_module.DockerCliError(
            "no docker", docker_cli_module.ERROR_DOCKER_CLI_NOT_FOUND
        )

    monkeypatch.setattr(core, "get_cli", _boom)

    infos = await core.list_agent_containers(1)

    assert [info.state for info in infos] == [CONTAINER_STATE_UNKNOWN, CONTAINER_STATE_UNKNOWN]
    assert [info.record_id for info in infos] == [1, 2]


async def test_list_skips_detection_when_caller_says_unavailable(fake, monkeypatch):
    """`docker_available=False` 由调用方给定时，**一次 docker 都不碰**（不 get_cli、不 ping）。

    `list_containers` 工具已经在工具层探过一次可用性，服务层再探就是重复的进程往返。
    """
    await _seed_record(container_name="agent-1-1", docker_id="c0ffee123456")

    def _boom():
        raise AssertionError("docker_available 已由调用方给出，不该再探测")

    monkeypatch.setattr(core, "get_cli", _boom)

    infos = await core.list_agent_containers(1, docker_available=False)

    assert [info.state for info in infos] == [CONTAINER_STATE_UNKNOWN]
    assert fake.calls() == []


async def test_list_marks_is_entered_only_for_current_container(fake):
    """`is_entered` 只对当前所处容器为 True（前端据此显示「使用中」）。"""
    await _seed_record(container_name="agent-1-1", docker_id="c0ffee123456")
    await _seed_record(container_name="agent-1-2", docker_id="deadbeef0002")
    fake.set("inspect", stdout=_inspect_json(status="exited"))

    infos = await core.list_agent_containers(1, current_container_id="deadbeef0002")

    assert [info.is_entered for info in infos] == [False, True]


async def test_list_excludes_soft_deleted_records(fake):
    """软删除的记录不再出现在列表里（但行还在库里留痕）。"""
    record = await _seed_record(docker_id="c0ffee123456")
    await core.gtAgentContainerManager.mark_deleted(record)
    fake.set("inspect", stdout=_inspect_json())

    assert await core.list_agent_containers(1) == []


async def test_list_is_scoped_to_agent(fake):
    """只返回自己的容器（别人的记录不可见）。"""
    await _seed_record(agent_id=2, container_name="agent-2-1", docker_id="deadbeef0002")
    fake.set("inspect", stdout=_inspect_json())

    assert await core.list_agent_containers(1) == []


# ---------------------------------------------------------------------------
# delete_container
# ---------------------------------------------------------------------------


async def test_delete_requires_exactly_one_identifier(fake):
    """`container_id` / `record_id` 必须二选一（都传或都不传都是 `container_invalid_params`）。"""
    with pytest.raises(TogoException) as excinfo:
        await core.delete_container(1)
    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS

    with pytest.raises(TogoException) as excinfo:
        await core.delete_container(1, container_id="c0ffee123456", record_id=1)
    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS


async def test_delete_by_docker_id_removes_container_and_soft_deletes(fake):
    """按 `docker_id` 删：执行 `rm -f <短ID>` 并标记软删除。"""
    record = await _seed_record(docker_id="c0ffee123456")

    deleted = await core.delete_container(1, container_id="c0ffee123456")

    assert deleted.id == record.id
    assert fake.calls_of("rm") == [["rm", "-f", "c0ffee123456"]]
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_by_record_id_works_for_pre_record(fake):
    """预记录（`docker_id IS NULL`）只能用 `record_id` 删（§2.1）。"""
    record = await _seed_record(container_name="agent-1-1")
    # 预记录按名 inspect 时容器不存在 → 跳过 rm，只标记 DB
    fake.set("inspect", stderr="Error: No such container: agent-1-1", returncode=1)

    deleted = await core.delete_container(1, record_id=record.id)

    assert deleted.id == record.id
    assert fake.calls_of("rm") == []
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_pre_record_removes_container_when_label_matches(fake):
    """预记录按名查到 label 匹配的容器时，best-effort `rm -f <容器名>`。"""
    record = await _seed_record(container_name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(
            status="created", labels={core.CONTAINER_LABEL_RECORD_ID: str(record.id)}
        ),
    )

    await core.delete_container(1, record_id=record.id)

    assert fake.calls_of("rm") == [["rm", "-f", "agent-1-1"]]


async def test_delete_pre_record_never_removes_container_with_foreign_label(fake):
    """**label 不匹配 ⇒ 绝不 `rm`**，只标记 DB 软删除（P-1 风险的核心护栏）。

    这条用例挂了就说明「误删外部同名容器」的风险回来了。
    """
    record = await _seed_record(container_name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(
            status="running", labels={core.CONTAINER_LABEL_RECORD_ID: "999"}
        ),
    )

    await core.delete_container(1, record_id=record.id)

    assert fake.calls_of("rm") == [], "绝不能对外部同名容器执行 rm"
    # 但我们自己的记录该照常软删除
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_rejects_container_in_use(fake):
    """正在使用的容器不许删（必须先 `exit_to_host`）。"""
    record = await _seed_record(docker_id="c0ffee123456")

    with pytest.raises(TogoException) as excinfo:
        await core.delete_container(1, container_id="c0ffee123456", current_container_id="c0ffee123456")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_IN_USE
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is not None


async def test_delete_rejects_other_agents_record_as_not_found(fake):
    """别人的记录按 record_id 删 ⇒ `container_not_found`（不泄露存在性）。"""
    record = await _seed_record(agent_id=2, container_name="agent-2-1", docker_id="deadbeef0002")

    with pytest.raises(TogoException) as excinfo:
        await core.delete_container(1, record_id=record.id)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOT_FOUND
    # 记录必须完好
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is not None


async def test_delete_treats_already_removed_container_as_success(fake):
    """`rm` 报「容器不存在」视为已达成目标（不报错、照常软删除）。"""
    record = await _seed_record(docker_id="c0ffee123456")
    fake.set("rm", stderr="Error: No such container: c0ffee123456", returncode=1)

    await core.delete_container(1, container_id="c0ffee123456")

    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_reports_failure_and_keeps_record_when_rm_fails(fake):
    """`rm` 因其他原因失败 ⇒ `container_delete_failed`，且**不**标记软删除（§5.4 第 4 步）。"""
    record = await _seed_record(docker_id="c0ffee123456")
    fake.set("rm", stderr="Error response from daemon: container is paused", returncode=1)

    with pytest.raises(TogoException) as excinfo:
        await core.delete_container(1, container_id="c0ffee123456")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_DELETE_FAILED
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is not None


# ---------------------------------------------------------------------------
# 批量清理：best-effort，绝不让残留记录永久占配额
# ---------------------------------------------------------------------------


async def test_delete_agent_containers_is_best_effort_and_marks_all(fake):
    """批量清理：单个 `rm` 失败也要继续，且所有记录最终都被标记软删除（§7）。"""
    await _seed_record(container_name="agent-1-1", docker_id="c0ffee123456")
    second = await _seed_record(container_name="agent-1-2")
    fake.set("rm", stderr="Error response from daemon: boom", returncode=1)
    fake.set("inspect", stderr="Error: No such container", returncode=1)

    deleted = await core.delete_agent_containers(1)

    assert deleted == 2
    assert await core.gtAgentContainerManager.list_active_by_agent(1) == []


async def test_delete_agent_containers_skips_current_container(fake):
    """批量清理跳过当前正在使用的容器（由调用方先切回宿主）。"""
    keep = await _seed_record(container_name="agent-1-1", docker_id="c0ffee123456")
    fake.set("inspect", stdout=_inspect_json())

    deleted = await core.delete_agent_containers(1, current_container_id="c0ffee123456")

    assert deleted == 0
    assert await core.gtAgentContainerManager.get_active_by_id(keep.id) is not None


async def test_delete_agent_containers_marks_db_even_when_docker_absent(fake, monkeypatch):
    """docker 不可用时也要标记 DB，避免记录永久占配额（§7 best-effort）。"""
    await _seed_record(container_name="agent-1-1", docker_id="c0ffee123456")

    from service.containerService import dockerCli as docker_cli_module

    def _boom():
        raise docker_cli_module.DockerCliError("no docker", docker_cli_module.ERROR_DOCKER_CLI_NOT_FOUND)

    monkeypatch.setattr(core, "get_cli", _boom)

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert await core.gtAgentContainerManager.list_active_by_agent(1) == []


async def test_delete_team_containers_covers_all_team_agents(fake):
    """Team 级清理覆盖该 Team 下所有 Agent 的容器（§7 Team 删除 / 改名）。"""
    from model.dbModel.gtAgent import GtAgent

    await GtAgent(
        team_id=1, name="阿张", role_template_id=1, i18n={}
    ).aio_save(force_insert=True)
    await _seed_record(agent_id=1, container_name="agent-1-1", docker_id="c0ffee123456")
    await _seed_record(agent_id=2, container_name="agent-2-1", docker_id="deadbeef0002")
    fake.set("rm", stdout="")

    deleted = await core.delete_team_containers(1)

    assert deleted == 2
    assert await core.gtAgentContainerManager.list_active_by_agent(1) == []
    assert await core.gtAgentContainerManager.list_active_by_agent(2) == []


async def test_count_containers_by_agent_ids_only_touches_db(fake):
    """列表页的容器数只查 DB，不调 docker（否则列表页会被 daemon 拖慢）。"""
    await _seed_record(agent_id=1, container_name="agent-1-1")
    await _seed_record(agent_id=1, container_name="agent-1-2")
    await _seed_record(agent_id=3, container_name="agent-3-1")

    counts = await core.count_containers_by_agent_ids([1, 2, 3])

    assert counts == {1: 2, 3: 1}
    assert fake.calls() == []


# ---------------------------------------------------------------------------
# prepare_container_gtsp
# ---------------------------------------------------------------------------


async def test_prepare_skips_copy_for_managed_container(fake):
    """自建容器在创建时已注入，进入前不再重复拷贝 6MB（§3.2）。"""
    from model.coreModel import ContainerTarget

    target = ContainerTarget(docker_id="c0ffee123456", is_managed=True, arch="arm64")

    await core.prepare_container_gtsp(target)

    assert fake.calls() == []


async def test_prepare_injects_for_external_container(fake):
    """外部容器进入前必须注入（它的镜像里没有 gtsp）。"""
    from model.coreModel import ContainerTarget

    target = ContainerTarget(docker_id="deadbeef0002", is_managed=False, arch="amd64")

    await core.prepare_container_gtsp(target)

    cp_calls = fake.calls_of("cp")
    assert len(cp_calls) == 1
    assert "gtsp-linux-amd64" in cp_calls[0][1]
    assert cp_calls[0][2] == "deadbeef0002:/tmp/gtsp"


async def test_prepare_reports_not_executable_when_copy_fails(fake):
    """注入失败 ⇒ `container_not_executable`，message 带 Docker 原始错误（§3.2）。"""
    from model.coreModel import ContainerTarget

    fake.set("cp", stderr="Error response from daemon: could not find /tmp", returncode=1)
    target = ContainerTarget(docker_id="deadbeef0002", is_managed=False, arch="arm64")

    with pytest.raises(TogoException) as excinfo:
        await core.prepare_container_gtsp(target)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOT_EXECUTABLE
    assert "could not find /tmp" in str(excinfo.value)
