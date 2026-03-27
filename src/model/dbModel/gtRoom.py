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
    agent_ids: list[int] = JsonField[list[int]](default=list)
    agent_read_index: dict[str, int] = JsonField[dict[str, int]](null=True)

    class Meta:
        table_name = "rooms"
        indexes = (
            (('team_id', 'name'), True),
        )


__all__ = ["GtRoom"]
