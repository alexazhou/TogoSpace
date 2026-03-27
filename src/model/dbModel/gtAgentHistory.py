from __future__ import annotations

import peewee

from .base import DbModelBase


class GtAgentHistory(DbModelBase):
    agent_id: int = peewee.IntegerField()
    seq: int = peewee.IntegerField(null=False)
    message_json: str = peewee.TextField(null=False)

    class Meta:
        table_name = "agent_histories"
        indexes = (
            (("agent_id", "seq"), True),
        )
