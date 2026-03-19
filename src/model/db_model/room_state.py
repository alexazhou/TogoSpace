from __future__ import annotations

import peewee

from .base import DbModelBase, JsonDictField


class RoomStateRecord(DbModelBase):
    room_key: str = peewee.CharField(primary_key=True)
    agent_read_index: dict[str, int] = JsonDictField(null=False)
    updated_at: str = peewee.CharField(null=False)

    class Meta:
        table_name = "room_states"
