"""批量清理路径（`_delete_records`）的删除标识防线（#111 裁定 7 的测试缺口）。

**为什么单独一份文件**：这条路径在 `c59b1843` 已接了真实调用方（Agent 离职/改名、
Team 改名/删除、目录迁移），一旦按名字误删就是批量事故。但 102 条用例零覆盖 ——
李哥把 `_delete_records` 的两条分支改成无条件 `docker rm -f <name>`，全绿零拦截。

这里测的不是「删了几个」，而是**用哪个标识删的**：
- 有 `docker_id` 的记录 → 必须 `rm -f <docker_id>`，绝不能碰容器名；
- 预记录（`docker_id IS NULL`）→ 按名 inspect + label 校验，label 不匹配**绝不 rm**。

每条用例都断言实际发出的 docker argv，而不是只看返回值 —— 返回值正确但 argv 按名删，
正是这次要防的形态。
"""

from __future__ import annotations

import json

import pytest

from service.containerService import core

# 复用 create 测试里的环境夹具（假 docker + 真实迁移 DDL + 临时库）
from .test_container_core import env as container_env  # noqa: F401


def _inspect_json(*, status: str = "exited", labels: dict | None = None) -> str:
    return json.dumps({
        "State": {"Status": status},
        "Image": "sha256:abc",
        "Config": {"Labels": labels or {}},
    })


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


async def _seed(agent_id: int = 1, name: str = "agent-1-1", docker_id: str | None = None):
    record = await core.gtAgentContainerManager.create(
        agent_id=agent_id, container_name=name, image="ubuntu:24.04"
    )
    if docker_id:
        await core.gtAgentContainerManager.set_docker_id(record, docker_id)
    return record


def _rm_calls(fake) -> list[list[str]]:
    return fake.calls_of("rm")


# ---------------------------------------------------------------------------
# 有 docker_id：只能按 ID 删
# ---------------------------------------------------------------------------


async def test_delete_agent_containers_uses_docker_id_not_name(fake):
    """有 `docker_id` 时必须 `rm -f <docker_id>`，argv 里不能出现容器名。

    这是 P-1 的主线：容器名不带 team 前缀，跨 Team 同名 Agent 可能重名，
    按名删会删掉别人的容器。
    """
    record = await _seed(name="agent-1-1", docker_id="c0ffee123456")
    fake.set("rm", stdout="")

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    rm_calls = _rm_calls(fake)
    assert rm_calls == [["rm", "-f", "c0ffee123456"]]
    assert "agent-1-1" not in rm_calls[0]
    # 软删除必须真的落库：只 rm 不标记，记录会永久占配额（§7）
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_agent_containers_never_inspects_own_docker_id(fake):
    """有 `docker_id` 时不需要按名 inspect：多一次 inspect 就多一处可被污染的判断。"""
    await _seed(name="agent-1-1", docker_id="c0ffee123456")
    fake.set("rm", stdout="")

    await core.delete_agent_containers(1)

    assert fake.calls_of("inspect") == []


async def test_delete_team_containers_uses_docker_id_for_every_record(fake):
    """批量清理多个 Agent 时每条都按自己的 `docker_id` 删，不串号。"""
    # team 清理是按「该 Team 的全部 Agent」反查记录，所以 agent 2 必须真的存在于库里
    from model.dbModel.gtAgent import GtAgent

    await GtAgent(team_id=1, name="小刚", role_template_id=1).aio_save(force_insert=True)
    await _seed(agent_id=1, name="agent-1-1", docker_id="c0ffee123456")
    await _seed(agent_id=2, name="agent-2-1", docker_id="deadbeef0002")
    fake.set("rm", stdout="")

    deleted = await core.delete_team_containers(1)

    assert deleted == 2
    assert _rm_calls(fake) == [
        ["rm", "-f", "c0ffee123456"],
        ["rm", "-f", "deadbeef0002"],
    ]


# ---------------------------------------------------------------------------
# 预记录（docker_id IS NULL）：按名 + label 校验
# ---------------------------------------------------------------------------


async def test_delete_agent_containers_removes_pre_record_when_label_matches(fake):
    """预记录的 label 匹配 ⇒ 按名 rm（这是唯一允许按名删的情形）。"""
    record = await _seed(name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(labels={core.CONTAINER_LABEL_RECORD_ID: str(record.id)}),
    )
    fake.set("rm", stdout="")

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert _rm_calls(fake) == [["rm", "-f", "agent-1-1"]]
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_agent_containers_never_removes_foreign_label(fake):
    """label 不匹配 ⇒ **绝不 rm**，只标记 DB 软删除。

    这就是「宿主上恰好有个同名容器是别人的」场景。按名 rm 会直接删掉别人的容器，
    且无法恢复 —— 这是本模块最严重的一类事故。
    """
    record = await _seed(name="agent-1-1")
    fake.set(
        "inspect",
        stdout=_inspect_json(labels={core.CONTAINER_LABEL_RECORD_ID: "999"}),
    )

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1  # DB 记录仍然要清掉（否则永久占配额）
    assert _rm_calls(fake) == []  # 但没有发出任何删除命令
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


async def test_delete_agent_containers_skips_rm_when_container_missing(fake):
    """容器确认不存在 ⇒ 不 rm（没什么可删的），仍标记软删除。"""
    await _seed(name="agent-1-1")
    fake.set("inspect", stderr="Error: No such container: agent-1-1", returncode=1)

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert _rm_calls(fake) == []


async def test_delete_agent_containers_does_not_rm_when_inspect_unreadable(fake):
    """inspect 输出解析不了 ⇒ 查不清，**不能按名 rm**（label 校验做不了）。

    这是 P-3 的直接后果：把「查不清」当成「容器不存在」时，这里会静默跳过，
    真实容器变成无人知晓的孤儿；而如果把「查不清」当成「容器在」，也会跳过。
    两种都不能 rm —— 宁可留一个需要人工检查的容器，也不能删别人的。
    """
    await _seed(name="agent-1-1")
    fake.set("inspect", stdout="not-a-json")

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert _rm_calls(fake) == []


# ---------------------------------------------------------------------------
# 正在使用的容器与失败降级
# ---------------------------------------------------------------------------


async def test_delete_agent_containers_skips_current_container(fake):
    """当前正在使用的容器跳过：删了会把正在跑的 turn 打断。"""
    await _seed(name="agent-1-1", docker_id="c0ffee123456")
    fake.set("rm", stdout="")

    deleted = await core.delete_agent_containers(1, current_container_id="c0ffee123456")

    assert deleted == 0
    assert _rm_calls(fake) == []
    # 记录也不能被标记删除（它还活着）
    assert await core.gtAgentContainerManager.list_active_by_agent(1) != []


async def test_delete_agent_containers_marks_db_even_when_rm_fails(fake):
    """rm 失败仍标记软删除：否则记录永久占配额，用户再也无法创建新容器（§7）。"""
    await _seed(name="agent-1-1", docker_id="c0ffee123456")
    fake.set("rm", stderr="Error response from daemon: device or resource busy", returncode=1)

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert await core.gtAgentContainerManager.list_active_by_agent(1) == []


async def test_delete_agent_containers_marks_db_when_docker_cli_missing(fake, monkeypatch):
    """docker CLI 都没有时只标记 DB，且**不发任何 docker 调用**。"""
    await _seed(name="agent-1-1", docker_id="c0ffee123456")

    def _no_cli():
        raise core.DockerCliError("docker 未安装", core.ERROR_DOCKER_CLI_NOT_FOUND)

    monkeypatch.setattr(core, "get_cli", _no_cli)

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert fake.calls() == []


async def test_delete_agent_containers_returns_zero_for_no_records(fake):
    """没有任何记录 ⇒ 0，且不调 docker。"""
    assert await core.delete_agent_containers(999) == 0
    assert fake.calls() == []
