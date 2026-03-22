from __future__ import annotations

import peewee

from .base import DbModelBase


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    max_function_calls: int = peewee.IntegerField(null=True)
    enabled: int = peewee.IntegerField(default=1)
    created_at: str = peewee.CharField()
    updated_at: str = peewee.CharField()

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]