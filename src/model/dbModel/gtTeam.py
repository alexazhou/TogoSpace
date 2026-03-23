from __future__ import annotations

from datetime import datetime

import peewee

from .base import DbModelBase


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    max_function_calls: int = peewee.IntegerField(default=5)
    enabled: int = peewee.IntegerField(default=1)
    created_at: str = peewee.CharField(default=lambda: datetime.now().isoformat())
    updated_at: str = peewee.CharField(default=lambda: datetime.now().isoformat())

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]
