from __future__ import annotations

import peewee

from .base import DbModelBase


class GtAgentHistory(DbModelBase):
    team_id: int = peewee.IntegerField()
    agent_name: str = peewee.CharField(null=False)
    seq: int = peewee.IntegerField(null=False)
    message_json: str = peewee.TextField(null=False)
    updated_at: str = peewee.CharField(null=False)

    class Meta:
        table_name = "agent_histories"
        indexes = (
            (("team_id", "agent_name", "seq"), True),
        )
