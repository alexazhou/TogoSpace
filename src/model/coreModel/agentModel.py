from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from constants import AgentStatus, DriverType, EmployStatus, ExecEnv
from model.coreModel.containerModel import ContainerInfo


class ExecEnvState(BaseModel):
    """某个 Agent 当前执行环境的快照。

    只存在于 Driver 内存，不落库：未加载的 Agent 一律回落到宿主。
    """

    model_config = ConfigDict(extra="forbid")

    exec_env: ExecEnv = ExecEnv.HOST
    current_container_id: str | None = None


class AgentInfo(BaseModel):
    """`GtAgent` 的 DB 字段视图。

    直接由 ORM 对象校验（`from_attributes`），不手抄列名；枚举的对外形态由
    serializer 固定，避免 pydantic 把 `auto()` 的数值写进响应。
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    team_id: int
    name: str
    role_template_id: int
    employ_status: EmployStatus
    """对外用枚举名（ON_BOARD / OFF_BOARD）。"""

    model: str
    driver: DriverType
    """对外用枚举值（native / claude_sdk / tsp）。"""

    employee_number: int
    i18n: dict[str, Any] = Field(default_factory=dict)
    allow_tools: list[str] | None = None
    allow_skills: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _serialize_datetime(self, value: datetime) -> str:
        """沿用接口原有时间格式（`jsonUtil` 默认的 `%Y-%m-%d %H:%M:%S.%f`）。"""
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")

    @field_serializer("employ_status")
    def _serialize_employ_status(self, value: EmployStatus) -> str:
        return value.name

    @field_serializer("driver")
    def _serialize_driver(self, value: DriverType) -> str:
        return value.value


class AgentViewInfo(BaseModel):
    """Agent 列表 / 详情的外层：原始 DB 视图 + 运行时状态。

    运行时字段放在 `agent` 外面，不混进 DB 字段里。
    """

    model_config = ConfigDict(extra="forbid")

    agent: AgentInfo
    status: AgentStatus
    """运行时状态（IDLE / ACTIVE / FAILED / CLOSED），来自内存而非 DB。"""

    exec_env: ExecEnv = ExecEnv.HOST
    current_container_id: str | None = None
    container_count: int = 0

    @field_serializer("status")
    def _serialize_status(self, value: AgentStatus) -> str:
        return value.name


class AgentListItem(AgentViewInfo):
    """列表页的一条 Agent 记录。"""

    special: str | None = None
    """特殊 Agent 标识（operator / system），仅特殊团队使用。"""


class AgentListInfo(BaseModel):
    """列表页响应体。"""

    model_config = ConfigDict(extra="forbid")

    agents: list[AgentListItem] = Field(default_factory=list)


class AgentDetailInfo(AgentViewInfo):
    """详情页响应体。"""

    error_message: str | None = None
    containers: list[ContainerInfo] = Field(default_factory=list)
