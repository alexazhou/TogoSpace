from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter

# 容器状态取值说明（不归一化，直接透传 Docker inspect 的 State.Status 原值）：
#   created / running / exited / paused / restarting / removing / dead …
# 另外两个是本系统补的哨兵值，用于「查不到」和「查失败」两种情形：
#   missing —— 容器不存在（外部删除 / 预记录尚未真正创建）
#   unknown —— inspect 本身失败（Docker 不可用、超时等），不能据此判定容器已丢
CONTAINER_STATE_MISSING = "missing"
CONTAINER_STATE_UNKNOWN = "unknown"

# 单字段的 JSON 序列化：必须与 `model_dump(mode="json")` **逐字节一致**，
# 否则同一字段在 `to_json()`（API/前端）和 `to_tool_dict()`（工具）里会呈现两种格式。
# 本仓 `created_at` 恒为 naive datetime（SQLite `datetime('now')`），但带时区时二者会分叉：
# `model_dump` 输出 `...Z`，而 `datetime.isoformat()` 输出 `...+00:00`。走同一个 adapter 保证一致。
_DATETIME_JSON: TypeAdapter[datetime | None] = TypeAdapter(datetime | None)


def _json_datetime(value: datetime | None) -> str | None:
    """把单个 `datetime` 转成与 `model_dump(mode="json")` 完全相同的字符串。"""
    return json.loads(_DATETIME_JSON.dump_json(value))


def to_container_id(docker_id: str | None) -> str | None:
    """单值场景的 `docker_id → container_id` 换名口径。

    为什么需要它（看着像恒等函数，但不是）：`ContainerInfo.to_tool_dict()` 只能覆盖
    「返回一整个容器对象」的场合，而 `create_container` / `enter_container` /
    `delete_container` 返回的是**单个 ID**，手上分别是 `GtAgentContainer` /
    `ContainerTarget` / `GtAgentContainer`，都不是 `ContainerInfo`。

    这三个点若各自手写 `"container_id": xxx.docker_id`，契约字段名就散落成 4 份拷贝，
    将来改名字必然漏一处——而漏掉的那处只会表现为「某个工具返回 null」，代码里看不出来。
    收敛到本函数后，`container_id` 这个对外名字在全仓只出现在这里和
    `ContainerInfo.to_tool_dict()`，两处相邻，改一处就能看到另一处。
    """
    return docker_id


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

    container_name: str | None = None
    """容器名（自建取记录里的 `container_name`，外部取 `docker inspect` 的 `Name` 去前导 `/`）。

    属于展示用途，**不参与连接决策**：连接只用 `docker_id`。放这里的原因只有一个——
    活动记录（§8）要显示容器名，而 `AgentTurnRunner` 同步读 Driver 时手上只有本结构体，
    为了一个名字去追加一次异步 DB/inspect 查询不值得。
    """

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
        """JSON 可序列化字典（`created_at` 由 `model_dump(mode="json")` 转成字符串）。

        给 controller / 前端用，保留模型原始字段名（`docker_id`）。
        """
        return self.model_dump(mode="json")

    def to_tool_dict(self) -> dict[str, Any]:
        """工具返回值专用字典（§6.2 `list_containers.containers[]`）。

        与 `to_json()` 的差别是**故意的**，两个方法不能合并：
        - 对内字段名是 `docker_id`，对模型暴露的契约名是 `container_id`（§6.2）；
        - 去掉内部字段 `agent_id`：模型只看自己的容器，多一个 ID 只会诱导它去猜
          别人的容器（也容易把 `agent_id` 误当成容器标识传进参数）。

        键是**显式白名单**，不是 `model_dump` 之后改键名：给 LLM 的契约必须逐个列出来，
        将来给模型加字段时自动漏出去就是多一份误导（多一个内部字段就多一个可猜的东西）。

        `container_id` 这个对外名字在全仓只出现在这里和 `to_container_id()` 两处，
        两者相邻（`to_container_id` 在本文件上方），改一处必然看到另一处。
        """
        return {
            "record_id": self.record_id,
            "container_id": to_container_id(self.docker_id),
            "container_name": self.container_name,
            "image": self.image,
            "state": self.state,
            "is_entered": self.is_entered,
            "created_at": _json_datetime(self.created_at),
        }


class ContainerCreateResult(BaseModel):
    """`create_container` 的成功返回体。"""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    container_name: str
    image: str
    state: str = "created"
    mounts: dict[str, str]
