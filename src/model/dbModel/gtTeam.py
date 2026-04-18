from __future__ import annotations

from typing import Any

import peewee

from .base import DbModelBase, JsonField


class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    uuid: str | None = peewee.CharField(null=True, unique=True)  # Preset 唯一标识，用于 UUID 去重
    config: dict[str, Any] = JsonField(default=dict)
    i18n: dict = JsonField(default=dict)  # 多语言数据，含 display_name 等
    enabled: bool = peewee.BooleanField(default=True)
    deleted: int = peewee.IntegerField(default=0)

    class Meta:
        table_name = "teams"


__all__ = ["GtTeam"]
