from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMessage(DbModelBase):
    id: int = peewee.AutoField()
    room_id: int = peewee.IntegerField(null=False)
    sender_name: str = peewee.CharField(null=False)
    content: str = peewee.TextField(null=False)
    send_time: str = peewee.CharField(null=False)

    class Meta:
        table_name = "room_messages"
        indexes = (
            (("room_id", "id"), False),
        )
