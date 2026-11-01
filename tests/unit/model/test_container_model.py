"""容器核心模型（`containerModel`）契约测试。

这些模型是 §8 接口约定的**唯一落地处**：前端 `types.ts`、工具 schema、WS 推送都
照它的 `model_dump(mode="json")` 对齐。所以这里钉的是「字段名 / 可空性 / 未知字段
是否放行」，而不是实现细节。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from model.coreModel import (
    CONTAINER_STATE_MISSING,
    CONTAINER_STATE_UNKNOWN,
    ContainerCreateResult,
    ContainerInfo,
    ContainerTarget,
)


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

    assert CONTAINER_STATE_MISSING == "missing"
    assert CONTAINER_STATE_UNKNOWN == "unknown"
    assert CONTAINER_STATE_MISSING not in docker_statuses
    assert CONTAINER_STATE_UNKNOWN not in docker_statuses


# ---------------------------------------------------------------------------
# ContainerTarget：Driver 连接目标（内部结构，不对外）
# ---------------------------------------------------------------------------


def test_container_target_display_name_distinguishes_managed_and_external():
    """日志短名必须一眼看出「自建 / 外部」与架构，避免排障时误判能删哪个容器。"""
    managed = ContainerTarget(docker_id="c0ffee123456", is_managed=True, arch="arm64")
    external = ContainerTarget(docker_id="deadbeef0001", is_managed=False, arch="amd64")

    assert managed.display_name == "c0ffee123456(managed,arm64)"
    assert external.display_name == "deadbeef0001(external,amd64)"


def test_container_target_forbids_extra_fields():
    """`extra="forbid"`：拼错字段名要立刻炸，而不是静默丢字段（`is_manegd` 这类）。"""
    with pytest.raises(ValidationError):
        ContainerTarget(docker_id="c0ffee123456", is_manegd=True, arch="arm64")


def test_container_target_container_name_is_optional():
    """`container_name` 是可选展示字段：没有它也必须能构造出连接目标。

    连接只依赖 `docker_id` / `is_managed` / `arch`。若把它写成必填，所有构造点
    （含外部容器拿不到 `Name` 的情况）都会失败，等于用一个日志字段卡住连接。
    """
    target = ContainerTarget(docker_id="c0ffee123456", is_managed=True, arch="arm64")
    assert target.container_name is None

    named = ContainerTarget(
        docker_id="c0ffee123456", is_managed=True, arch="arm64", container_name="agent-a-1"
    )
    assert named.container_name == "agent-a-1"


# ---------------------------------------------------------------------------
# ContainerInfo：对外展示记录
# ---------------------------------------------------------------------------


def test_container_info_pre_record_allows_null_docker_id():
    """预记录（`docker_id IS NULL`）必须能构造出来——它在列表里是可见的一行。

    若 `docker_id` 被写成必填，创建中的容器会从列表里消失（或构造直接抛错），
    用户看不到「正在创建」的那一行，也就无法用 `record_id` 删除它。
    """
    info = ContainerInfo(
        record_id=12,
        agent_id=3,
        docker_id=None,
        container_name="agent-a-1",
        image="ubuntu:24.04",
    )

    assert info.docker_id is None
    assert info.record_id == 12
    # 运行时字段有安全默认值：查不到状态时是 unknown，而不是空串
    assert info.state == CONTAINER_STATE_UNKNOWN
    assert info.is_entered is False
    assert info.created_at is None


def test_container_info_json_dump_serializes_created_at_and_keeps_null_docker_id():
    """`to_json()` 必须是 JSON 安全的：`datetime` 转字符串、`None` 保持 `null`。

    这条直接对应前端契约——`created_at` 传成 `datetime` 对象会导致 WS 序列化失败，
    而 `docker_id: null` 是「预记录」的契约标记，不能变成空串。
    """
    info = ContainerInfo(
        record_id=12,
        agent_id=3,
        docker_id=None,
        container_name="agent-a-1",
        image="ubuntu:24.04",
        state=CONTAINER_STATE_MISSING,
        is_entered=False,
        created_at=datetime(2026, 11, 1, 1, 30, 0),
    )

    payload = info.to_json()

    assert payload["docker_id"] is None
    assert payload["state"] == CONTAINER_STATE_MISSING
    assert payload["created_at"] == "2026-11-01T01:30:00"
    assert isinstance(payload["created_at"], str)
    # 字段集合与前端 `ContainerInfo` 接口一一对应
    assert set(payload) == {
        "record_id", "agent_id", "docker_id", "container_name",
        "image", "state", "is_entered", "created_at",
    }


def test_container_info_exposes_record_id_not_container_id():
    """对外主键叫 `record_id`（= 表主键），不是 `container_id`。

    前端表格行 key 与 `delete_container(record_id=...)` 都依赖这个名字；
    写成 `container_id` 会和「12 位短 ID」混淆，且预记录时它必然是空的。
    """
    assert "record_id" in ContainerInfo.model_fields
    assert "container_id" not in ContainerInfo.model_fields


def test_container_info_forbids_extra_fields():
    """未知字段一律拒绝：防止 service 层塞了前端不认识的字段还以为契约打通了。"""
    with pytest.raises(ValidationError):
        ContainerInfo(
            record_id=1, agent_id=1, docker_id=None,
            container_name="agent-a-1", image="ubuntu:24.04", is_managed=True,
        )


# ---------------------------------------------------------------------------
# ContainerCreateResult：create_container 成功返回体
# ---------------------------------------------------------------------------


def test_container_create_result_defaults_state_to_created():
    """`create_container` 只创建不启动，初始状态恒为 `created`（不是 running）。"""
    result = ContainerCreateResult(
        container_id="c0ffee123456",
        container_name="agent-a-1",
        image="ubuntu:24.04",
        mounts={"/workspace/self": "/host/self", "/workspace/team_shared": "/host/shared"},
    )

    assert result.state == "created"
    assert set(result.mounts) == {"/workspace/self", "/workspace/team_shared"}


def test_container_create_result_forbids_extra_fields():
    """返回体字段集合固定，多塞字段说明有人在别处偷偷扩契约。"""
    with pytest.raises(ValidationError):
        ContainerCreateResult(
            container_id="c0ffee123456",
            container_name="agent-a-1",
            image="ubuntu:24.04",
            mounts={},
            exec_env="container",  # 创建不切换环境，这个字段不该出现在这里
        )


# ---------------------------------------------------------------------------
# to_tool_dict / to_json：两个映射方法的职责分离（#111 裁定 1）
# ---------------------------------------------------------------------------


def test_to_tool_dict_uses_container_id_and_drops_agent_id():
    """工具契约（§6.2）的字段名是 `container_id`，且不该暴露内部字段 `agent_id`。

    这不只是「名字不同」：模型看到的字段名决定它下一次工具调用传什么参数。
    给它 `docker_id` 会让它以为参数名也叫 `docker_id`，而 `enter_container` /
    `delete_container` 的参数名是 `container_id`——模型照抄返回值就会传错。
    """
    info = ContainerInfo(
        record_id=7,
        agent_id=3,
        docker_id="c0ffee123456",
        container_name="agent-3-1",
        image="ubuntu:24.04",
        state="exited",
        is_entered=True,
    )

    as_tool = info.to_tool_dict()

    assert as_tool["container_id"] == "c0ffee123456"
    assert "docker_id" not in as_tool
    assert "agent_id" not in as_tool
    assert as_tool["record_id"] == 7
    assert as_tool["is_entered"] is True


def test_to_tool_dict_keeps_null_container_id_for_pre_record():
    """预记录的 `container_id` 必须是 null（不是省略字段）：模型靠它判断「还没有 ID」。"""
    info = ContainerInfo(
        record_id=8, agent_id=3, docker_id=None, container_name="agent-3-2", image="ubuntu:24.04"
    )

    as_tool = info.to_tool_dict()

    assert "container_id" in as_tool
    assert as_tool["container_id"] is None
    assert as_tool["container_name"] == "agent-3-2"


def test_to_json_keeps_internal_field_names_for_api():
    """`to_json()` 保留内部字段名给 controller / 前端（前端契约就是 `docker_id`）。"""
    info = ContainerInfo(
        record_id=7, agent_id=3, docker_id="c0ffee123456", container_name="agent-3-1",
        image="ubuntu:24.04",
    )

    as_api = info.to_json()

    assert as_api["docker_id"] == "c0ffee123456"
    assert as_api["agent_id"] == 3
    assert "container_id" not in as_api


def test_to_tool_dict_serializes_created_at_to_iso_string():
    """`created_at` 必须是字符串：`datetime` 直接塞进工具返回值会让 JSON 序列化失败。"""
    info = ContainerInfo(
        record_id=7, agent_id=3, docker_id="c0ffee123456", container_name="agent-3-1",
        image="ubuntu:24.04", created_at=datetime(2026, 11, 1, 10, 0, 0),
    )

    as_tool = info.to_tool_dict()

    assert isinstance(as_tool["created_at"], str)
    assert as_tool["created_at"].startswith("2026-11-01")


# ---------------------------------------------------------------------------
# to_container_id：单值场景的换名口径
# ---------------------------------------------------------------------------


def test_to_container_id_passes_through_both_forms():
    """单值换名对 None 与字符串都要原样透传（预记录没有被删的 ID，就是 None）。"""
    from model.coreModel import to_container_id

    assert to_container_id("c0ffee123456") == "c0ffee123456"
    assert to_container_id(None) is None
