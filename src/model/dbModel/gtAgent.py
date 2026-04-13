from __future__ import annotations

import peewee

from constants import EmployStatus, DriverType
from .base import DbModelBase, EnumField, JsonField


class GtAgent(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField(null=False)
    role_template_id: int = peewee.IntegerField(null=False)
    employ_status: EmployStatus = EnumField(EmployStatus, default=EmployStatus.ON_BOARD)
    model: str = peewee.CharField(default="")
    driver: DriverType = EnumField(DriverType, default=DriverType.NATIVE)
    employee_number: int = peewee.IntegerField(default=0)
    i18n: dict = JsonField(default=dict)  # 多语言数据，含 display_name

    class Meta:
        table_name = "agents"
        indexes = (
            (("team_id", "name"), False),  # 非唯一索引，允许离职成员名字被复用
            (("team_id", "employee_number"), True),
        )
