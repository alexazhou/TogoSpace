from __future__ import annotations

import peewee

from .base import DbModelBase


class RoomStateRecord(DbModelBase):
    room_key: str = peewee.CharField(primary_key=True)
    agent_read_index_json: str = peewee.TextField(null=False)
    updated_at: str = peewee.CharField(null=False)

    class Meta:
        table_name = "room_states"
