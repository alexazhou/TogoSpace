from __future__ import annotations

import json
from datetime import datetime

import peewee

from .base import DbModelBase


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    working_directory: str = peewee.CharField(default="")
    config: str = peewee.TextField(default="{}")
    max_function_calls: int = peewee.IntegerField(default=5)
    enabled: int = peewee.IntegerField(default=1)
    created_at: str = peewee.CharField(default=lambda: datetime.now().isoformat())
    updated_at: str = peewee.CharField(default=lambda: datetime.now().isoformat())

    def get_config(self) -> dict:
        try:
            return json.loads(self.config or "{}")
        except json.JSONDecodeError:
            return {}

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]
