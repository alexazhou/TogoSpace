"""Agent 长期记忆的三层认知视图模型（L1 / L2 / L3）与对外序列化口径。

**为什么需要显式序列化**（不要指望 `jsonUtil.json_dump`）：
`jsonUtil` 遇到 `Enum` 统一取 `obj.name`（见 `src/util/jsonUtil.py` 的
`convert_to_builtin_type`），而 `EnumField` 落库也是 `name`。也就是说，若直接把
`GtAgentMemory` 丢给 JSON dump，对外会得到 `"star_level": "SUMMARY"`。

对外契约要的是**小写 value**（step2 §2.2「保留固定小写字符串 value」，与
`agentModel.AgentRuntimeInfo._serialize_agent` 对 `driver` 的处理是同一个先例：
`data["driver"] = value.driver.value`）。所以这里每个模型都用 `field_serializer`
把枚举写到 `.value`，HTTP 侧走 `memory_api_dict()` 同样口径。

DB 存 `name`、对外出 `value`、模板读 `mem.star_level.value` —— 三者各有明确定义，
改动任何一处都会破坏一处契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer

from constants import MemoryStarLevel, MemoryStatus
from model.dbModel.gtAgentMemory import GtAgentMemory
from util import jsonUtil


class _MemorySerializers(BaseModel):
    """三层视图共用的枚举 / 时间序列化口径（子类只管字段）。"""

    model_config = ConfigDict(extra="forbid")

    @staticmethod
    def _enum_value(value: MemoryStarLevel | MemoryStatus | None) -> str | None:
        return None if value is None else value.value


class MemoryListItem(_MemorySerializers):
    """L1 目录层：只让 Agent 知道「记过哪些主题」及其星标状态，成本最低。"""

    id: int
    title: str
    star_level: MemoryStarLevel
    tags: list[str] = []
    updated_at: datetime

    @field_serializer("star_level")
    def _serialize_star_level(self, value: MemoryStarLevel) -> str:
        return value.value

    @field_serializer("updated_at")
    def _serialize_updated_at(self, value: datetime) -> str | None:
        return jsonUtil.datetime_to_pydantic_json(value)

    @classmethod
    def from_memory(cls, memory: GtAgentMemory) -> "MemoryListItem":
        return cls(
            id=memory.id,
            title=memory.title,
            star_level=memory.star_level,
            tags=list(memory.tags or []),
            updated_at=memory.updated_at,
        )


class MemorySearchItem(_MemorySerializers):
    """L2 候选层：单句结论 + 命中正文的关键片段，让 Agent 不必先读全文。"""

    id: int
    title: str
    summary: str
    snippet: str
    star_level: MemoryStarLevel
    tags: list[str] = []
    updated_at: datetime

    @field_serializer("star_level")
    def _serialize_star_level(self, value: MemoryStarLevel) -> str:
        return value.value

    @field_serializer("updated_at")
    def _serialize_updated_at(self, value: datetime) -> str | None:
        return jsonUtil.datetime_to_pydantic_json(value)

    @classmethod
    def from_memory(cls, memory: GtAgentMemory, snippet: str) -> "MemorySearchItem":
        return cls(
            id=memory.id,
            title=memory.title,
            summary=memory.summary,
            snippet=snippet,
            star_level=memory.star_level,
            tags=list(memory.tags or []),
            updated_at=memory.updated_at,
        )


class MemoryDetail(_MemorySerializers):
    """L3 正文层：完整字段。仅 `memory_read` / `memory_create` / `memory_update` 返回。"""

    id: int
    title: str
    summary: str
    content: str
    star_level: MemoryStarLevel
    status: MemoryStatus
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime

    @field_serializer("star_level")
    def _serialize_star_level(self, value: MemoryStarLevel) -> str:
        return value.value

    @field_serializer("status")
    def _serialize_status(self, value: MemoryStatus) -> str:
        return value.value

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str | None:
        return jsonUtil.datetime_to_pydantic_json(value)

    @field_serializer("updated_at")
    def _serialize_updated_at(self, value: datetime) -> str | None:
        return jsonUtil.datetime_to_pydantic_json(value)

    @classmethod
    def from_memory(cls, memory: GtAgentMemory) -> "MemoryDetail":
        return cls(
            id=memory.id,
            title=memory.title,
            summary=memory.summary,
            content=memory.content,
            star_level=memory.star_level,
            status=memory.status,
            tags=list(memory.tags or []),
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )


def memory_api_dict(memory: GtAgentMemory) -> dict[str, Any]:
    """HTTP 接口的单条记忆字典：复用 ORM 序列化后，把两个枚举覆写成对外小写 value。

    与 `agentModel.AgentRuntimeInfo._serialize_agent` 完全同一个套路——**不能**
    直接返回 `memory.to_json()`，那会把枚举 name（`SUMMARY` / `ACTIVE`）漏给前端。
    """
    data = jsonUtil.object_to_json_data(memory)
    data["star_level"] = memory.star_level.value
    data["status"] = memory.status.value
    return data
