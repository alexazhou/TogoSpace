from __future__ import annotations

import peewee

from constants import EmployStatus, DriverType
from util import i18nUtil
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

    @property
    def display_name(self) -> str:
        """返回 Agent 的显示名称（从 i18n.display_name 解析，缺省回退到 name）。"""
        return i18nUtil.extract_i18n_str(
            self.i18n.get("display_name") if self.i18n else None,
            default=self.name,
        ) or self.name

    class Meta:
        table_name = "agents"
        indexes = (
            (("team_id", "name"), False),  # 非唯一索引，允许离职成员名字被复用
            (("team_id", "employee_number"), True),
        )
