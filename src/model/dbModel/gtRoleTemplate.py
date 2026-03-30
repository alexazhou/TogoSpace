from __future__ import annotations

import peewee

from constants import DriverType, RoleTemplateType
from .base import DbModelBase, EnumField, JsonField


class GtRoleTemplate(DbModelBase):
    name: str = peewee.CharField(unique=True)
    model: str | None = peewee.CharField(null=True)
    soul: str = peewee.TextField(default="")
    type: RoleTemplateType = EnumField(RoleTemplateType, default=RoleTemplateType.SYSTEM)
    driver: DriverType | None = EnumField(DriverType, null=True)
    allowed_tools: list[str] | None = JsonField[list[str]](null=True)

    class Meta:
        table_name = "role_templates"


__all__ = ["GtRoleTemplate"]
