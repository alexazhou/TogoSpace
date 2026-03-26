from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMember(DbModelBase):
    room_id: int = peewee.IntegerField()
    member_id: int = peewee.IntegerField()

    class Meta:
        table_name = "room_members"
