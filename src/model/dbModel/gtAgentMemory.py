from __future__ import annotations

import peewee

from constants import MemoryStarLevel, MemoryStatus

from .base import DbModelBase, EnumField, JsonField


class GtAgentMemory(DbModelBase):
    """Agent 长期记忆槽位（单 Agent 私有，严格按 team_id + agent_id 隔离）。

    一槽一主题：`title` 唯一主题（单行 ≤ 80 字符）、`summary` 单句结论（≤ 120）、
    `content` 完整正文（≤ 1000）。长度 / 同名防重 / 星标配额等规则一律在
    `service.memoryService` 集中校验，本模型与表结构不做 DB 层约束。

    `star_level` / `status` 用 `EnumField`，DB 与 JSON 口径都是枚举 **name**
    （NONE/TITLE/SUMMARY、ACTIVE/ARCHIVED），详见 `constants.MemoryStarLevel`。
    """

    team_id:    int               = peewee.IntegerField()
    agent_id:   int               = peewee.IntegerField()
    title:      str               = peewee.TextField()
    summary:    str               = peewee.TextField()
    content:    str               = peewee.TextField()
    star_level: MemoryStarLevel   = EnumField(MemoryStarLevel, default=MemoryStarLevel.NONE)
    status:     MemoryStatus      = EnumField(MemoryStatus, default=MemoryStatus.ACTIVE)
    tags:       list[str]         = JsonField(default=list)

    class Meta:
        table_name = "agent_memories"
        indexes = (
            (("team_id", "agent_id", "status"), False),
            (("team_id", "agent_id", "star_level"), False),
        )
