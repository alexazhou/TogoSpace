from __future__ import annotations

import peewee

from constants import RoleTemplateType
from .base import DbModelBase, EnumField, JsonField


class GtRoleTemplate(DbModelBase):
    name: str = peewee.CharField(unique=True)
    model: str | None = peewee.CharField(null=True)
    soul: str = peewee.TextField(default="")
    type: RoleTemplateType = EnumField(RoleTemplateType, default=RoleTemplateType.SYSTEM)
    allowed_tools: list[str] | None = JsonField(null=True)
    i18n: dict = JsonField(default=dict)  # 多语言数据，含 display_name

    class Meta:
        table_name = "role_templates"


__all__ = ["GtRoleTemplate"]
