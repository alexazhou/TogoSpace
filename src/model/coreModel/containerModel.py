from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from util import jsonUtil
from constants import CONTAINER_STATE_UNKNOWN


class ContainerTarget(BaseModel):
    """底层连接目标：只描述「连哪个容器、怎么连」，不承载展示字段。

    由 `containerService.get_container_target` 组装，供 `TspAgentDriver.connect` 使用。
    """

    model_config = ConfigDict(extra="forbid")

    container_id: str
    """Docker 容器 12 位短 ID。"""

    is_managed: bool
    """True: 当前 Agent 在 `agent_containers` 里的自建容器；False: 外部 / 他人容器。

    运行中重连发现容器不存在时，据 `is_managed` 决定是否软删除记录。
    """

    arch: str
    """架构（amd64 / arm64），用于挑选注入容器的那份 Linux gtsp 产物。"""

    container_name: str | None = None
    """容器名（自建取记录里的 `container_name`，外部取 `docker inspect` 的 `Name` 去前导 `/`）。

    属于展示用途，**不参与连接决策**：连接只用 `container_id`。放这里的原因只有一个——
    活动记录（§8）要显示容器名，而 `AgentTurnRunner` 同步读 Driver 时手上只有本结构体，
    为了一个名字去追加一次异步 DB/inspect 查询不值得。
    """

    @property
    def display_name(self) -> str:
        """日志用短名，避免每个日志点都自己拼格式。"""
        return f"{self.container_id}({'managed' if self.is_managed else 'external'},{self.arch})"


class ContainerRecordInfo(BaseModel):
    """`agent_containers` 的对外视图：`id → record_id`，隐藏内部字段。

    用 `from_attributes` 直接从 `GtAgentContainer` 读取，字段声明即序列化契约：
    未声明的 `deleted` / `updated_at` 不会出现在响应里，`created_at` 也由 pydantic
    自动转成 ISO 字符串——不需要再手写 field_serializer。
    """

    model_config = ConfigDict(from_attributes=True)

    record_id: int = Field(validation_alias="id")
    agent_id: int
    container_id: str | None
    """Docker 容器 12 位短 ID；预记录尚未回填时为 None。"""
    container_name: str
    image: str
    created_at: datetime | None


class ContainerInfo(BaseModel):
    """自建容器的一条对外展示记录（`list_containers` / Agent 容器接口共用）。

    外层只放运行时字段，DB 记录放在 `container` 里（`ContainerRecordInfo`），
    `model_dump(mode="json")` 即可直接作为 API 响应。
    """

    model_config = ConfigDict(extra="forbid")

    container: ContainerRecordInfo
    """`agent_containers` 的活跃记录（`container_id IS NULL` 表示创建中的预记录）。"""

    state: str = CONTAINER_STATE_UNKNOWN
    """Docker 原始状态；查不到为 missing，inspect 失败为 unknown。"""

    is_entered: bool = False
    """该容器是否为该 Agent 当前所处的执行环境。"""

    def to_tool_dict(self) -> dict[str, Any]:
        """工具返回值专用字典（§6.2 `list_containers.containers[]`）。

        与 API 的 `model_dump()` 差别是**故意的**，两者不能合并：
        - 去掉内部字段 `agent_id`：模型只看自己的容器，多一个 ID 只会诱导它去猜
          别人的容器（也容易把 `agent_id` 误当成容器标识传进参数）；
        - `state` / `is_entered` 是运行时字段，不属于 DB 记录，这里平铺到同一层。

        键是**显式白名单**，不是 `model_dump` 之后改键名：给 LLM 的契约必须逐个列出来，
        将来给模型加字段时自动漏出去就是多一份误导（多一个内部字段就多一个可猜的东西）。
        """
        record = self.container
        return {
            "record_id": record.record_id,
            "container_id": record.container_id,
            "container_name": record.container_name,
            "image": record.image,
            "state": self.state,
            "is_entered": self.is_entered,
            "created_at": jsonUtil.datetime_to_pydantic_json(record.created_at),
        }


class ContainerCreateResult(BaseModel):
    """`create_container` 的成功返回体。"""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    container_name: str
    image: str
    state: str = "created"
    mounts: dict[str, str]
