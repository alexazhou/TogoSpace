from __future__ import annotations

from datetime import datetime
import json

import peewee
import peewee_async
from constants import EnhanceEnum

_database_proxy: peewee.DatabaseProxy = peewee.DatabaseProxy()


def bind_database(database: peewee.Database) -> None:
    _database_proxy.initialize(database)


class JsonDictField(peewee.TextField):
    """将 dict 与 TEXT(JSON) 自动互转。"""

    def db_value(self, value):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def python_value(self, value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)


class EnumField(peewee.CharField):
    """枚举字段，用于在数据库中存储 EnhanceEnum 的 name。"""

    def __init__(self, enum_cls: type[EnhanceEnum], *args, **kwargs):
        self.enum = enum_cls
        super(EnumField, self).__init__(*args, **kwargs)

    def db_value(self, value: EnhanceEnum) -> str:
        if value is None:
            return None
        return value.name

    def python_value(self, value) -> EnhanceEnum:
        if value is None or value == "":
            return None
        return getattr(self.enum, value)


class DbModelBase(peewee_async.AioModel):
    @classmethod
    def _now_iso(cls) -> str:
        return datetime.now().isoformat()

    @classmethod
    def _has_updated_at_field(cls) -> bool:
        return "updated_at" in cls._meta.fields

    @classmethod
    def _inject_updated_at(cls, payload: dict) -> dict:
        if cls._has_updated_at_field() and "updated_at" not in payload:
            payload["updated_at"] = cls._now_iso()
        return payload

    @classmethod
    def insert(cls, *args, **kwargs):
        if kwargs:
            kwargs = cls._inject_updated_at(dict(kwargs))
            return super().insert(*args, **kwargs)
        if args and isinstance(args[0], dict):
            first = cls._inject_updated_at(dict(args[0]))
            return super().insert(first, *args[1:], **kwargs)
        return super().insert(*args, **kwargs)

    @classmethod
    def insert_many(cls, rows, fields=None):
        if cls._has_updated_at_field():
            rows = [
                cls._inject_updated_at(dict(row)) if isinstance(row, dict) else row
                for row in rows
            ]
        return super().insert_many(rows, fields=fields)

    @classmethod
    def update(cls, *args, **kwargs):
        if cls._has_updated_at_field():
            if kwargs:
                if "updated_at" not in kwargs:
                    kwargs = dict(kwargs)
                    kwargs["updated_at"] = cls._now_iso()
                return super().update(*args, **kwargs)
            if args and isinstance(args[0], dict):
                first = dict(args[0])
                if "updated_at" not in first:
                    first["updated_at"] = cls._now_iso()
                return super().update(first, *args[1:], **kwargs)
        return super().update(*args, **kwargs)

    class Meta:
        database = _database_proxy
        legacy_table_names = False
