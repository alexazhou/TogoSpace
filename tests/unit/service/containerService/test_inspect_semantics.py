"""`docker inspect` 三种结果的语义区分（#111 裁定 3 / P-3）。

**这一条是本模块最容易埋雷的地方**：`_inspect_container` 有三种可能结果，
调用方对它们的反应完全不同：

| 结果 | 含义 | 上层应有的反应 |
|---|---|---|
| `dict` | 查到了 | 正常解析状态 / 镜像 |
| `None` | **确认容器不存在**（Docker 明确回 no such） | `container_lost`、跳过 rm |
| `_INSPECT_UNKNOWN` | 有输出但解析不了 / 空输出 → **查不清** | `unknown`、绝不软删记录、绝不按名 rm |

把「查不清」当成「不存在」的后果（改动前的实际行为，李哥实测过）：
1. `enter_container` 报 `container_lost` → 上层去软删记录，而容器其实还在；
2. `list_containers` 显示 `missing` → 用户以为容器没了；
3. **删除预记录时 `rm` 根本没发** → 真实容器变成孤儿，还占着宿主资源。

把「查不清」当成「查到了」也不好：`label` 校验会拿到空 labels 而判成「不是我的」，
同样跳过 rm。所以正确做法只能是「不删、明确报 unknown、留人工检查的线索」。
"""

from __future__ import annotations

import json

import pytest

from exception import TogoException
from model.coreModel import CONTAINER_STATE_MISSING, CONTAINER_STATE_UNKNOWN
from service.containerService import core

from .test_container_core import env as container_env  # noqa: F401


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


def _inspect_ok(*, status: str = "exited", labels: dict | None = None) -> str:
    return json.dumps({
        "State": {"Status": status},
        "Image": "sha256:abc",
        "Config": {"Labels": labels or {}},
    })


async def _seed(docker_id: str | None = None, name: str = "agent-1-1"):
    record = await core.gtAgentContainerManager.create(
        agent_id=1, container_name=name, image="ubuntu:24.04"
    )
    if docker_id:
        await core.gtAgentContainerManager.set_docker_id(record, docker_id)
    return record


# ---------------------------------------------------------------------------
# _inspect_container 的三分结果
# ---------------------------------------------------------------------------


async def test_inspect_returns_dict_on_success(fake):
    """成功 ⇒ 直接返回 dict（不走任何哨兵）。"""
    fake.set("inspect", stdout=_inspect_ok(status="running"))

    data = await core._inspect_container(core.get_cli(), "c0ffee123456")

    assert isinstance(data, dict)
    assert data["State"]["Status"] == "running"


async def test_inspect_returns_none_only_for_explicit_no_such(fake):
    """`no such container` ⇒ None（**唯一**可以断言「容器不存在」的情况）。"""
    fake.set("inspect", stderr="Error: No such container: c0ffee123456", returncode=1)

    assert await core._inspect_container(core.get_cli(), "c0ffee123456") is None


async def test_inspect_returns_unknown_sentinel_for_unparseable_output(fake):
    """输出解析不了 ⇒ 查不清哨兵，**不是 None**（None 会让上层去软删记录）。"""
    fake.set("inspect", stdout="<<<garbage>>>")

    result = await core._inspect_container(core.get_cli(), "c0ffee123456")

    assert result is core._INSPECT_UNKNOWN
    assert result is not None


async def test_inspect_returns_unknown_sentinel_for_empty_output(fake):
    """退出码 0 但没输出 ⇒ 查不清哨兵。"""
    fake.set("inspect", stdout="   \n")

    result = await core._inspect_container(core.get_cli(), "c0ffee123456")

    assert result is core._INSPECT_UNKNOWN


async def test_inspect_returns_unknown_sentinel_for_non_object_json(fake):
    """输出是合法 JSON 但不是对象（例如数组）⇒ 查不清，不能当容器不存在。"""
    fake.set("inspect", stdout="[]")

    result = await core._inspect_container(core.get_cli(), "c0ffee123456")

    assert result is core._INSPECT_UNKNOWN


async def test_inspect_raises_for_daemon_unavailable(fake):
    """daemon 不可用 ⇒ 抛 `DockerCliError`（三种返回之外，由调用方决定降级方式）。"""
    fake.set(
        "inspect",
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
        returncode=1,
    )

    with pytest.raises(core.DockerCliError):
        await core._inspect_container(core.get_cli(), "c0ffee123456")


# ---------------------------------------------------------------------------
# 上层对「查不清」的反应：绝不软删、绝不按名删
# ---------------------------------------------------------------------------


async def test_get_target_reports_enter_failed_not_container_lost_for_unreadable(fake):
    """查不清时不能报 `container_lost`：那会让上层去软删一条其实还活着的记录。"""
    record = await _seed(docker_id="c0ffee123456")
    fake.set("inspect", stdout="garbage")

    with pytest.raises(TogoException) as excinfo:
        await core.get_container_target(1, "c0ffee123456")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_ENTER_FAILED
    assert excinfo.value.error_code != core.ERROR_CONTAINER_LOST
    # 记录必须还在
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is not None


async def test_list_shows_unknown_not_missing_for_unreadable(fake):
    """列表页显示 `unknown`（查不清），**不是** `missing`（没了）——两者对用户含义完全不同。"""
    await _seed(docker_id="c0ffee123456")
    fake.set("inspect", stdout="garbage")

    infos = await core.list_agent_containers(1)

    assert infos[0].state == CONTAINER_STATE_UNKNOWN
    assert infos[0].state != CONTAINER_STATE_MISSING


async def test_list_shows_missing_for_confirmed_absent(fake):
    """对照组：确认不存在才是 `missing`（证明上面那条不是「永远 unknown」）。"""
    await _seed(docker_id="c0ffee123456")
    fake.set("inspect", stderr="Error: No such container: c0ffee123456", returncode=1)

    infos = await core.list_agent_containers(1)

    assert infos[0].state == CONTAINER_STATE_MISSING


async def test_delete_pre_record_does_not_rm_when_inspect_unreadable(fake):
    """**P-3 的核心后果**：查不清时按名 rm 会删掉别人的同名容器，所以必须跳过。

    改动前这里被当成「容器不存在」直接 return —— 表面看「很安全」，实际是
    真实容器漏删成孤儿；而如果实现改成「查不清就按名删」，则会误删别人的容器。
    正确行为是两者都不做：不 rm，只标记 DB 软删除，并留 error 级日志。
    """
    record = await _seed(name="agent-1-1")
    fake.set("inspect", stdout="garbage")
    fake.set("rm", stdout="")

    deleted = await core.delete_agent_containers(1)

    assert deleted == 1
    assert fake.calls_of("rm") == []
    # 记录已软删除（否则永久占配额）
    assert await core.gtAgentContainerManager.get_active_by_id(record.id) is None


# ---------------------------------------------------------------------------
# 兜底错误码由调用方决定（#111 裁定 2 / P-2）
# ---------------------------------------------------------------------------


async def test_classify_uses_caller_fallback_for_unknown_stderr(fake):
    """归不出来的 stderr ⇒ 用调用方给的兜底码，而不是写死的 `container_create_failed`。"""
    exc = core.DockerCliError("boom", stderr="some weird docker error")

    assert core._classify_docker_error(exc, core.ERROR_CONTAINER_ENTER_FAILED) == (
        core.ERROR_CONTAINER_ENTER_FAILED
    )
    assert core._classify_docker_error(exc, core.ERROR_CONTAINER_CREATE_FAILED) == (
        core.ERROR_CONTAINER_CREATE_FAILED
    )


async def test_enter_path_reports_enter_failed_not_create_failed(fake):
    """进入路径的兜底码必须是 `container_enter_failed`（模型据此换做法，报错就白费）。"""
    record = await _seed(docker_id="c0ffee123456")
    assert record is not None
    fake.set("inspect", stderr="Error response from daemon: something unexpected", returncode=1)

    with pytest.raises(TogoException) as excinfo:
        await core.get_container_target(1, "c0ffee123456")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_ENTER_FAILED
    assert excinfo.value.error_code != core.ERROR_CONTAINER_CREATE_FAILED


async def test_classify_still_recognizes_known_errors():
    """兜底码只在「归不出来」时使用：认得出来的错误码不能被兜底吃掉。"""
    mount = core.DockerCliError("x", stderr="is not shared from the host")
    assert core._classify_docker_error(mount, core.ERROR_CONTAINER_ENTER_FAILED) == (
        core.ERROR_CONTAINER_MOUNT_DENIED
    )

    missing_image = core.DockerCliError("x", stderr="no such image: ubuntu:nope")
    assert core._classify_docker_error(missing_image, core.ERROR_CONTAINER_ENTER_FAILED) == (
        core.ERROR_CONTAINER_IMAGE_MISSING
    )
