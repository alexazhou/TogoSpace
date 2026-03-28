from __future__ import annotations

import peewee

from constants import EmployStatus, DriverType
from .base import DbModelBase, EnumField


class GtAgent(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField(null=False)
    role_template_name: str = peewee.CharField(null=False)
    employ_status: EmployStatus = EnumField(EmployStatus, default=EmployStatus.ON_BOARD)
    model: str = peewee.CharField(default="")
    driver: DriverType = EnumField(DriverType, default=DriverType.NATIVE)
    employee_number: int = peewee.IntegerField(default=0)

    class Meta:
        table_name = "agents"
        indexes = (
            (("team_id", "name"), True),
            (("team_id", "employee_number"), True),
        )
