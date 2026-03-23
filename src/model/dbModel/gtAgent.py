from __future__ import annotations

import peewee

from .base import DbModelBase


class GtAgent(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField(null=False)
    model: str = peewee.CharField(default="")
    updated_at: str = peewee.CharField()

    class Meta:
        table_name = "agents"
        indexes = (
            (("team_id", "name"), True),
        )


__all__ = ["GtAgent"]
