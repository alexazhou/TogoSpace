from __future__ import annotations

import peewee

from .base import DbModelBase


class GtAgent(DbModelBase):
    template_name: str = peewee.CharField(unique=True)
    model: str = peewee.CharField(default="")

    class Meta:
        table_name = "agents"


__all__ = ["GtAgent"]
