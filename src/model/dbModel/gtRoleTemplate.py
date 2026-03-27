from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoleTemplate(DbModelBase):
    template_name: str = peewee.CharField(unique=True)
    model: str = peewee.CharField(default="")

    class Meta:
        table_name = "role_templates"


__all__ = ["GtRoleTemplate"]
