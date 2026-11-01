from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from util import jsonUtil
from constants import ContainerState


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


class ContainerInfo(BaseModel):
    """自建容器的一条对外展示记录（`list_containers` / Agent 容器接口共用）。

    字段是 `agent_containers` 活跃记录的对外投影（`id → record_id`，隐藏内部字段
    `deleted` / `updated_at`）加上运行时字段，平铺成一层，`model_dump(mode="json")`
    即可直接作为 API 响应。
    """

    model_config = ConfigDict(extra="forbid")

    record_id: int
    """`agent_containers.id`，预记录没有 Docker ID 时用它删除。"""

    agent_id: int
    container_id: str | None
    """Docker 容器 12 位短 ID；预记录尚未回填时为 None。"""
    container_name: str
    image: str
    created_at: datetime | None
    """记录创建时间（`agent_containers.created_at`），序列化为 ISO 8601 字符串。"""

    state: ContainerState = ContainerState.UNKNOWN
    """Docker 原始状态；查不到为 missing，inspect 失败为 unknown。"""

    is_entered: bool = False
    """该容器是否为该 Agent 当前所处的执行环境。"""

    def to_tool_dict(self) -> dict[str, Any]:
        """工具返回值专用字典（§6.2 `list_containers.containers[]`）。

        与 API 的 `model_dump()` 差别是**故意的**，两者不能合并：
        去掉内部字段 `agent_id`：模型只看自己的容器，多一个 ID 只会诱导它去猜
        别人的容器（也容易把 `agent_id` 误当成容器标识传进参数）。

        键是**显式白名单**，不是 `model_dump` 之后改键名：给 LLM 的契约必须逐个列出来，
        将来给模型加字段时自动漏出去就是多一份误导（多一个内部字段就多一个可猜的东西）。
        """
        return {
            "record_id": self.record_id,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "image": self.image,
            "state": self.state.value,
            "is_entered": self.is_entered,
            "created_at": jsonUtil.datetime_to_pydantic_json(self.created_at),
        }
