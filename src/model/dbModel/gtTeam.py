from __future__ import annotations

import json
from typing import Any

import peewee

from .base import DbModelBase, JsonField


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    config: dict[str, Any] = JsonField[dict[str, Any]](default=dict)
    max_function_calls: int = peewee.IntegerField(default=5)
    enabled: int = peewee.IntegerField(default=1)
    deleted: int = peewee.IntegerField(default=0)

    def get_config(self) -> dict[str, Any]:
        if isinstance(self.config, dict):
            return self.config
        if self.config is None:
            return {}
        if not isinstance(self.config, str):
            return {}
        try:
            return json.loads(self.config or "{}")
        except json.JSONDecodeError:
            return {}

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]
