from __future__ import annotations

import peewee

from constants import SystemConfigKey
from .base import DbModelBase, EnumField


class GtSystemConfig(DbModelBase):
    """系统配置表，key/value 结构。"""
    key: SystemConfigKey = EnumField(SystemConfigKey, unique=True)
    value: str = peewee.TextField(default="")

    class Meta:
        table_name = "system_configs"


__all__ = ["GtSystemConfig"]