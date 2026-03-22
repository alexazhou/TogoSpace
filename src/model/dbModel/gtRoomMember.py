from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMember(DbModelBase):
    room_key: str = peewee.CharField()
    member_name: str = peewee.CharField()

    class Meta:
        table_name = "room_members"
        indexes = (
            (('room_key', 'member_name'), True),
        )