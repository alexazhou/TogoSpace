from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMember(DbModelBase):
    room_id: int = peewee.IntegerField()
    agent_name: str = peewee.CharField()

    class Meta:
        table_name = "room_members"