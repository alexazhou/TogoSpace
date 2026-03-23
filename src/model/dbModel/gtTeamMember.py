from __future__ import annotations

import peewee

from .base import DbModelBase


class GtTeamMember(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField(null=False)
    agent_name: str = peewee.CharField(null=False)
    updated_at: str = peewee.CharField()

    class Meta:
        table_name = "team_members"
        indexes = (
            (("team_id", "name"), True),
        )
