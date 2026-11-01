from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer

from constants import ContainerState
from model.dbModel.gtAgentContainer import GtAgentContainer
from util import jsonUtil


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


class ContainerRuntimeInfo(BaseModel):
    """自建容器的运行时信息（`list_containers` / Agent 容器接口共用）。

    直接持有 DB 记录 `record`，不再重复声明 `agent_id` / `container_id` /
    `container_name` / `image` / `created_at`；`state` / `is_entered` 是实时查询
    得到的运行时字段。`model_dump(mode="json")` 即可直接作为 API 响应。
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    record: GtAgentContainer
    """`agent_containers` 的活跃记录；`record.id` 即预记录删除用的 `record_id`。"""

    state: ContainerState = ContainerState.UNKNOWN
    """Docker 原始状态；查不到为 missing，inspect 失败为 unknown。"""

    is_entered: bool = False
    """该容器是否为该 Agent 当前所处的执行环境。"""

    @field_serializer("record")
    def _serialize_record(self, value: GtAgentContainer) -> dict[str, Any]:
        data = jsonUtil.object_to_json_data(value)
        # 列表只含活跃记录：软删标记与更新时间属于内部字段，不对外
        data.pop("deleted", None)
        data.pop("updated_at", None)
        # 保持与旧契约一致的 ISO 8601 格式，并与 to_tool_dict() 逐字节一致
        data["created_at"] = jsonUtil.datetime_to_pydantic_json(value.created_at)
        return data

    def to_tool_dict(self) -> dict[str, Any]:
        """工具返回值专用字典（§6.2 `list_containers.containers[]`）。

        与 API 的 `model_dump()` 差别是**故意的**，两者不能合并：
        把 DB 主键映射为 `record_id`，并去掉内部字段 `agent_id`：模型只看自己的
        容器，多一个 ID 只会诱导它去猜别人的容器（也容易把 `agent_id` 误当成容器
        标识传进参数）。

        键是**显式白名单**，不是 `model_dump` 之后改键名：给 LLM 的契约必须逐个列出来，
        将来给模型加字段时自动漏出去就是多一份误导（多一个内部字段就多一个可猜的东西）。
        """
        record = self.record
        return {
            "record_id": record.id,
            "container_id": record.container_id,
            "container_name": record.container_name,
            "image": record.image,
            "state": self.state.value,
            "is_entered": self.is_entered,
            "created_at": jsonUtil.datetime_to_pydantic_json(record.created_at),
        }
