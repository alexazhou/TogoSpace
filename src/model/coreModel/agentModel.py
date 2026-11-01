from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from constants import AgentStatus, ExecEnv
from model.dbModel.gtAgent import GtAgent
from util import jsonUtil


class ExecEnvState(BaseModel):
    """某个 Agent 当前执行环境的快照。

    只存在于 Driver 内存，不落库：未加载的 Agent 一律回落到宿主。
    """

    model_config = ConfigDict(extra="forbid")

    exec_env: ExecEnv = ExecEnv.HOST
    current_container_id: str | None = None


class AgentRuntimeInfo(BaseModel):
    """Agent 列表 / 详情的外层：原始 DB 对象 + 运行时状态。

    `agent` 直接持有 `GtAgent`，序列化复用 ORM 自带的 `to_json()`；运行时字段
    放在外层，不混进 DB 字段里。执行环境整体用 `ExecEnvState` 表达。
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    agent: GtAgent
    status: AgentStatus
    """运行时状态（IDLE / ACTIVE / FAILED / CLOSED），来自内存而非 DB。"""

    exec_env_state: ExecEnvState = Field(default_factory=ExecEnvState)
    container_count: int = 0

    @field_serializer("agent")
    def _serialize_agent(self, value: GtAgent) -> dict[str, Any]:
        data = jsonUtil.object_to_json_data(value)
        # 接口契约要求 driver 用小写 value，jsonUtil 默认取枚举的 name
        data["driver"] = value.driver.value
        return data

    @field_serializer("status")
    def _serialize_status(self, value: AgentStatus) -> str:
        return value.name
