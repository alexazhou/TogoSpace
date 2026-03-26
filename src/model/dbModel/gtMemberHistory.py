from __future__ import annotations

import peewee

from .base import DbModelBase


class GtMemberHistory(DbModelBase):
    team_id: int = peewee.IntegerField()
    member_name: str = peewee.CharField(null=False)
    seq: int = peewee.IntegerField(null=False)
    message_json: str = peewee.TextField(null=False)
    updated_at: str = peewee.CharField(null=False)

    class Meta:
        table_name = "member_histories"
        indexes = (
            (("team_id", "member_name", "seq"), True),
        )
