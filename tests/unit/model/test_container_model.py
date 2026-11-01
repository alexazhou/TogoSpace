"""容器核心模型（`containerModel`）契约测试。

这些模型是 §8 接口约定的**唯一落地处**：前端 `types.ts`、工具 schema、WS 推送都
照它的 `model_dump(mode="json")` 对齐。所以这里钉的是「字段名 / 可空性 / 未知字段
是否放行」，而不是实现细节。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from constants import ContainerState
from model.coreModel import ContainerInfo, ContainerTarget
from model.dbModel.gtAgentContainer import GtAgentContainer


# ---------------------------------------------------------------------------
# 哨兵值：必须与 Docker `State.Status` 的原值不重叠
# ---------------------------------------------------------------------------


def test_state_sentinels_do_not_collide_with_docker_status_values():
    """`missing` / `unknown` 是本系统补的哨兵，不能和 Docker 真实状态撞名。

    撞名会让「查不到容器」和「容器 dead」无法区分，前端也会把缺失当成真实状态展示。
    """
    docker_statuses = {
        "created", "running", "paused", "restarting", "removing",
        "exited", "dead", "stopping",
    }

    # 枚举 value 是前后端协议的一部分：必须保持小写原值，不能改成 auto()
    assert ContainerState.MISSING.value == "missing"
    assert ContainerState.UNKNOWN.value == "unknown"
    assert ContainerState.MISSING.value not in docker_statuses
    assert ContainerState.UNKNOWN.value not in docker_statuses


# ---------------------------------------------------------------------------
# ContainerTarget：Driver 连接目标（内部结构，不对外）
# ---------------------------------------------------------------------------


def test_container_target_display_name_distinguishes_managed_and_external():
    """日志短名必须一眼看出「自建 / 外部」与架构，避免排障时误判能删哪个容器。"""
    managed = ContainerTarget(container_id="c0ffee123456", is_managed=True, arch="arm64")
    external = ContainerTarget(container_id="deadbeef0001", is_managed=False, arch="amd64")

    assert managed.display_name == "c0ffee123456(managed,arm64)"
    assert external.display_name == "deadbeef0001(external,amd64)"


def test_container_target_forbids_extra_fields():
    """`extra="forbid"`：拼错字段名要立刻炸，而不是静默丢字段（`is_manegd` 这类）。"""
    with pytest.raises(ValidationError):
        ContainerTarget(container_id="c0ffee123456", is_manegd=True, arch="arm64")


def test_container_target_container_name_is_optional():
    """`container_name` 是可选展示字段：没有它也必须能构造出连接目标。

    连接只依赖 `container_id` / `is_managed` / `arch`。若把它写成必填，所有构造点
    （含外部容器拿不到 `Name` 的情况）都会失败，等于用一个日志字段卡住连接。
    """
    target = ContainerTarget(container_id="c0ffee123456", is_managed=True, arch="arm64")
    assert target.container_name is None

    named = ContainerTarget(
        container_id="c0ffee123456", is_managed=True, arch="arm64", container_name="agent-a-1"
    )
    assert named.container_name == "agent-a-1"


# ---------------------------------------------------------------------------
# ContainerInfo：对外展示记录
# ---------------------------------------------------------------------------


def _record(**overrides) -> GtAgentContainer:
    fields: dict = {
        "id": 7,
        "agent_id": 3,
        "container_id": "c0ffee123456",
        "container_name": "agent-3-1",
        "image": "ubuntu:24.04",
        "created_at": None,
        "updated_at": None,
    }
    fields.update(overrides)
    return GtAgentContainer(**fields)


def _info(record: GtAgentContainer | None = None, **overrides) -> ContainerInfo:
    fields: dict = {"record": record or _record()}
    fields.update(overrides)
    return ContainerInfo(**fields)


def test_container_info_pre_record_allows_null_container_id():
    """预记录（DB `container_id IS NULL`）必须能构造出来——它在列表里是可见的一行。

    若 `container_id` 被写成必填，创建中的容器会从列表里消失（或构造直接抛错），
    用户看不到「正在创建」的那一行，也就无法用 `record.id` 删除它。
    """
    info = _info(_record(id=12, container_id=None, container_name="agent-a-1"))

    assert info.record.container_id is None
    assert info.record.id == 12
    # 运行时字段有安全默认值：查不到状态时是 unknown，而不是空串
    assert info.state is ContainerState.UNKNOWN
    assert info.is_entered is False


def test_container_info_json_dump_serializes_created_at_and_keeps_null_container_id():
    """`model_dump(mode="json")` 必须是 JSON 安全的：`datetime` 转字符串、`None` 保持 `null`。

    这条直接对应前端契约——`created_at` 传成 `datetime` 对象会导致 WS 序列化失败，
    而 `container_id: null` 是「预记录」的契约标记，不能变成空串。
    """
    info = _info(
        _record(
            id=12,
            container_id=None,
            container_name="agent-a-1",
            created_at=datetime(2026, 11, 1, 1, 30, 0),
        ),
        state=ContainerState.MISSING,
    )

    payload = info.model_dump(mode="json")

    assert payload["record"]["container_id"] is None
    assert payload["state"] == ContainerState.MISSING.value
    assert payload["record"]["created_at"] == "2026-11-01T01:30:00"
    assert isinstance(payload["record"]["created_at"], str)
    # DB 记录整体在 `record` 里，运行时字段在外层；deleted / updated_at 是内部字段
    assert set(payload) == {"record", "state", "is_entered"}
    assert set(payload["record"]) == {
        "id", "agent_id", "container_id", "container_name", "image", "created_at",
    }


def test_container_info_exposes_record_id_and_container_id_separately():
    """`record.id` 是表主键，`record.container_id` 是 Docker 短 ID，两者不能混用。

    前端表格行 key 与 `delete_container(record_id=...)` 依赖表主键；
    `container_id` 在预记录时必然为 null，不能当主键用。
    """
    payload = _info().model_dump(mode="json")
    assert payload["record"]["id"] == 7
    assert payload["record"]["container_id"] == "c0ffee123456"


def test_container_info_forbids_extra_fields():
    """未知字段一律拒绝：防止 service 层塞了前端不认识的字段还以为契约打通了。"""
    with pytest.raises(ValidationError):
        _info(is_managed=True)


# ---------------------------------------------------------------------------
# to_tool_dict / model_dump：两个映射方法的职责分离（#111 裁定 1）
# ---------------------------------------------------------------------------


def test_to_tool_dict_uses_container_id_and_drops_agent_id():
    """工具契约（§6.2）使用 `container_id`，且不该暴露内部字段 `agent_id`。

    模型看到什么字段名，下一次工具调用就照着传：`enter_container` /
    `delete_container` 的参数名都是 `container_id`，返回值必须同名。
    """
    info = _info(state=ContainerState.EXITED, is_entered=True)

    as_tool = info.to_tool_dict()

    assert as_tool["container_id"] == "c0ffee123456"
    assert "agent_id" not in as_tool
    assert set(as_tool) == {
        "record_id", "container_id", "container_name",
        "image", "state", "is_entered", "created_at",
    }
    assert as_tool["record_id"] == 7
    assert as_tool["is_entered"] is True


def test_to_tool_dict_keeps_null_container_id_for_pre_record():
    """预记录的 `container_id` 必须是 null（不是省略字段）：模型靠它判断「还没有 ID」。"""
    info = _info(_record(id=8, container_id=None, container_name="agent-3-2"))

    as_tool = info.to_tool_dict()

    assert "container_id" in as_tool
    assert as_tool["container_id"] is None
    assert as_tool["container_name"] == "agent-3-2"


def test_model_dump_uses_container_id_for_api():
    """API 把 DB 记录放在 `record` 里，并保留 `agent_id` 等 DB 字段。"""
    as_api = _info().model_dump(mode="json")

    assert as_api["record"]["container_id"] == "c0ffee123456"
    assert as_api["record"]["agent_id"] == 3
    assert as_api["record"]["id"] == 7


def test_to_tool_dict_serializes_created_at_to_iso_string():
    """`created_at` 必须是字符串：`datetime` 直接塞进工具返回值会让 JSON 序列化失败。"""
    info = _info(_record(created_at=datetime(2026, 11, 1, 10, 0, 0)))

    as_tool = info.to_tool_dict()

    assert isinstance(as_tool["created_at"], str)
    assert as_tool["created_at"].startswith("2026-11-01")


def test_to_tool_dict_created_at_matches_api_serialization_exactly():
    """工具层与 API 层的 `created_at` 必须**逐字节一致**。

    `to_tool_dict()` 不能用 `datetime.isoformat()` 代替：带时区时 pydantic 输出 `...Z`，
    而 `isoformat()` 输出 `...+00:00`。同一字段在两个方法里两种格式，前端和模型看到的
    就是两个不同的值——这种差异只在带时区时才出现，最容易漏测。
    """
    cases = [
        None,
        datetime(2026, 11, 1, 10, 0, 0),
        datetime(2026, 11, 1, 10, 0, 0, 123456),
        datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    ]

    for created_at in cases:
        info = _info(_record(container_id=None, created_at=created_at))

        assert info.to_tool_dict()["created_at"] == info.model_dump(mode="json")["record"]["created_at"], created_at


def test_to_tool_dict_created_at_uses_z_suffix_for_utc_not_offset():
    """钉死 UTC 的具体形态：`...Z`（pydantic 口径），不是 `...+00:00`（isoformat 口径）。"""
    info = _info(_record(container_id=None, created_at=datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)))

    assert info.to_tool_dict()["created_at"] == "2026-11-01T10:00:00Z"
    assert info.to_tool_dict()["created_at"] != "2026-11-01T10:00:00+00:00"
