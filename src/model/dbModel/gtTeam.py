from __future__ import annotations

import json

import peewee

from .base import DbModelBase


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    config: str = peewee.TextField(default="{}")
    max_function_calls: int = peewee.IntegerField(default=5)
    enabled: int = peewee.IntegerField(default=1)
    deleted: int = peewee.IntegerField(default=0)

    def get_config(self) -> dict:
        try:
            return json.loads(self.config or "{}")
        except json.JSONDecodeError:
            return {}

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]
