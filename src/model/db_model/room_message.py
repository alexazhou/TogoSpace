from __future__ import annotations

import peewee

from .base import DbModelBase


class RoomMessageRecord(DbModelBase):
    id: int = peewee.AutoField()
    room_key: str = peewee.CharField(null=False)
    team_name: str = peewee.CharField(null=False)
    sender_name: str = peewee.CharField(null=False)
    content: str = peewee.TextField(null=False)
    send_time: str = peewee.CharField(null=False)

    class Meta:
        table_name = "room_messages"
        indexes = (
            (("room_key", "id"), False),
        )
