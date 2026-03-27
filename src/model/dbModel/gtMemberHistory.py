from __future__ import annotations

import peewee

from .base import DbModelBase


class GtMemberHistory(DbModelBase):
    member_id: int = peewee.IntegerField()
    seq: int = peewee.IntegerField(null=False)
    message_json: str = peewee.TextField(null=False)

    class Meta:
        table_name = "member_histories"
        indexes = (
            (("member_id", "seq"), True),
        )
