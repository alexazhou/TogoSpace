from __future__ import annotations

import peewee

from .base import DbModelBase, EnumField, JsonDictField
from constants import RoomType


class GtRoom(DbModelBase):
    room_id: str = peewee.CharField(unique=True)
    team_id: str = peewee.CharField()
    name: str = peewee.CharField()
    type: RoomType = EnumField(RoomType, null=False)
    initial_topic: str = peewee.CharField(null=True)
    max_turns: int = peewee.IntegerField(default=100)
    agent_read_index: dict[str, int] = JsonDictField(null=True)
    updated_at: str = peewee.CharField()

    class Meta:
        table_name = "rooms"
        indexes = (
            (('team_id', 'name'), True),
        )


__all__ = ["GtRoom"]
