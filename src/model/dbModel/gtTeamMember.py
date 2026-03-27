from __future__ import annotations

import peewee

from constants import EmployStatus
from .base import DbModelBase, EnumField


class GtTeamMember(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField(null=False)
    agent_name: str = peewee.CharField(null=False)
    employ_status: EmployStatus = EnumField(EmployStatus, default=EmployStatus.ON_BOARD)
    model: str = peewee.CharField(default="")
    driver: str = peewee.TextField(default="{}")

    class Meta:
        table_name = "team_members"
        indexes = (
            (("team_id", "name"), True),
        )
