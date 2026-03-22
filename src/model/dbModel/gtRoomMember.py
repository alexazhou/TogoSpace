from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMember(DbModelBase):
    room_id: str = peewee.CharField()
    member_name: str = peewee.CharField()

    class Meta:
        table_name = "room_members"
        indexes = (
            (('room_id', 'member_name'), True),
        )