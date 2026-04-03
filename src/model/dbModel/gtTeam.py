from __future__ import annotations

from typing import Any

import peewee

from .base import DbModelBase, JsonField


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    config: dict[str, Any] = JsonField[dict[str, Any]](default=dict)
    enabled: int = peewee.IntegerField(default=1)
    deleted: int = peewee.IntegerField(default=0)

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]
