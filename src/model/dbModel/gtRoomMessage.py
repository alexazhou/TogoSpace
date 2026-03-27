from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMessage(DbModelBase):
    room_id: int = peewee.IntegerField(null=False)
    agent_id: int = peewee.IntegerField(null=False, default=0)
    content: str = peewee.TextField(null=False)
    send_time: str = peewee.CharField(null=False)

    class Meta:
        table_name = "room_messages"
