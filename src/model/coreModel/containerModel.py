from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# 容器状态取值说明（不归一化，直接透传 Docker inspect 的 State.Status 原值）：
#   created / running / exited / paused / restarting / removing / dead …
# 另外两个是本系统补的哨兵值，用于「查不到」和「查失败」两种情形：
#   missing —— 容器不存在（外部删除 / 预记录尚未真正创建）
#   unknown —— inspect 本身失败（Docker 不可用、超时等），不能据此判定容器已丢
CONTAINER_STATE_MISSING = "missing"
CONTAINER_STATE_UNKNOWN = "unknown"


class ContainerTarget(BaseModel):
    """底层连接目标：只描述「连哪个容器、怎么连」，不承载展示字段。

    由 `containerService.get_container_target` 组装，供 `TspAgentDriver.connect` 使用。
    """

    model_config = ConfigDict(extra="forbid")

    docker_id: str
    """Docker 容器 12 位短 ID（与对外的 `container_id` 相同）。"""

    is_managed: bool
    """True: 当前 Agent 在 `agent_containers` 里的自建容器；False: 外部 / 他人容器。

    运行中重连发现容器不存在时，据 `is_managed` 决定是否软删除记录。
    """

    arch: str
    """架构（amd64 / arm64），用于挑选注入容器的那份 Linux gtsp 产物。"""

    @property
    def display_name(self) -> str:
        """日志用短名，避免每个日志点都自己拼格式。"""
        return f"{self.docker_id}({'managed' if self.is_managed else 'external'},{self.arch})"


class ContainerInfo(BaseModel):
    """自建容器的一条对外展示记录（`list_containers` / Agent 详情页共用）。

    对应 `agent_containers` 的活跃记录；`docker_id IS NULL` 的预记录 `container_id` 为 None。
    """

    model_config = ConfigDict(extra="forbid")

    record_id: int
    """`agent_containers.id`，预记录没有 Docker ID 时用它删除。"""

    agent_id: int
    docker_id: str | None = None
    """12 位短 ID；预记录尚未回填时为 None。"""

    container_name: str
    image: str

    # 以下为运行时字段，不落库
    state: str = CONTAINER_STATE_UNKNOWN
    """Docker 原始状态；查不到为 missing，inspect 失败为 unknown。"""

    is_entered: bool = False
    """该容器是否为该 Agent 当前所处的执行环境。"""

    created_at: datetime | None = None
    """记录创建时间（`agent_containers.created_at`）。

    设计文档 §2.1 写的是 `int` 时间戳，但仓库 `DbModelBase` 强制该字段为
    `datetime`（`AutoTimestampMixin` 自动注入），这里按仓库约定透传 `datetime`，
    `model_dump(mode="json")` 会序列化成 ISO 8601 字符串给前端。
    """

    def to_json(self) -> dict[str, Any]:
        """JSON 可序列化字典（`created_at` 由 `model_dump(mode="json")` 转成字符串）。"""
        return self.model_dump(mode="json")


class ContainerCreateResult(BaseModel):
    """`create_container` 的成功返回体。"""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    container_name: str
    image: str
    state: str = "created"
    mounts: dict[str, str]
