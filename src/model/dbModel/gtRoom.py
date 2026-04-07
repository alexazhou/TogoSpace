from __future__ import annotations

import peewee

from .base import DbModelBase, EnumField, JsonField
from constants import RoomType


class GtRoom(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField()
    type: RoomType = EnumField(RoomType, null=False)
    initial_topic: str = peewee.CharField(null=True)
    max_turns: int = peewee.IntegerField(default=100)
    agent_ids: list[int] = JsonField(default=list)
    agent_read_index: dict[str, int] = JsonField(null=True)
    turn_pos: int = peewee.IntegerField(default=0)  # 当前发言位索引，重启后恢复
    biz_id: str | None = peewee.CharField(null=True)  # 业务标识，如 "DEPT:123"
    tags: list[str] = JsonField(default=list)  # 标签列表

    class Meta:
        table_name = "rooms"
        indexes = (
            (('team_id', 'name'), True),
        )


__all__ = ["GtRoom"]
