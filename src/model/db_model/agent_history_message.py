from __future__ import annotations

import peewee

from .base import DbModelBase


class AgentHistoryMessageRecord(DbModelBase):
    id: int = peewee.AutoField()
    agent_key: str = peewee.CharField(null=False)
    seq: int = peewee.IntegerField(null=False)
    message_json: str = peewee.TextField(null=False)
    updated_at: str = peewee.CharField(null=False)

    class Meta:
        table_name = "agent_history_messages"
        indexes = (
            (("agent_key", "seq"), True),
        )
