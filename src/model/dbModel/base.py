from __future__ import annotations

from datetime import datetime
import json
from typing import Generic, TypeVar, cast

import peewee
import peewee_async
from constants import EnhanceEnum

TJson = TypeVar("TJson")
TEnum = TypeVar("TEnum", bound="EnhanceEnum")

_database_proxy: peewee.DatabaseProxy = peewee.DatabaseProxy()


def bind_database(database: peewee.Database) -> None:
    _database_proxy.initialize(database)


class JsonField(peewee.TextField, Generic[TJson]):
    """将 JSON 值（dict/list 等）与 TEXT(JSON) 自动互转。"""

    def db_value(self, value: TJson | None) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def python_value(self, value) -> TJson | None:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return cast(TJson, value)
        return cast(TJson, json.loads(value))


class EnumField(peewee.CharField, Generic[TEnum]):
    """枚举字段，用于在数据库中存储 EnhanceEnum 的 name。

    用法与 JsonField 一致，通过构造时传入枚举类来绑定类型：
        EnumField(EmployStatus, default=EmployStatus.ON_BOARD)
    """

    def __init__(self, enum_cls: type[TEnum], *args, **kwargs):
        self.enum = enum_cls
        super(EnumField, self).__init__(*args, **kwargs)

    def db_value(self, value: TEnum | None) -> str | None:
        if value is None:
            return None
        return value.name

    def python_value(self, value) -> TEnum | None:
        if value is None or value == "":
            return None
        return cast(TEnum, getattr(self.enum, value))


class DbModelBase(peewee_async.AioModel):
    id:         int = peewee.AutoField()
    created_at: datetime = peewee.DateTimeField(default=datetime.now)
    updated_at: datetime = peewee.DateTimeField(default=datetime.now)

    @classmethod
    def _now(cls) -> datetime:
        return datetime.now()

    @classmethod
    def _inject_insert_timestamps(cls, payload: dict) -> dict:
        now = cls._now()
        if "created_at" not in payload:
            payload["created_at"] = now
        if "updated_at" not in payload:
            payload["updated_at"] = now
        return payload

    @classmethod
    def _inject_updated_at(cls, payload: dict) -> dict:
        if "updated_at" not in payload:
            payload["updated_at"] = cls._now()
        return payload

    @classmethod
    def insert(cls, *args, **kwargs):
        if kwargs:
            kwargs = cls._inject_insert_timestamps(dict(kwargs))
            return super().insert(*args, **kwargs)
        if args and isinstance(args[0], dict):
            first = cls._inject_insert_timestamps(dict(args[0]))
            return super().insert(first, *args[1:], **kwargs)
        return super().insert(*args, **kwargs)

    @classmethod
    def insert_many(cls, rows, fields=None):
        rows = [
            cls._inject_insert_timestamps(dict(row)) if isinstance(row, dict) else row
            for row in rows
        ]
        return super().insert_many(rows, fields=fields)

    @classmethod
    def update(cls, *args, **kwargs):
        if kwargs:
            if "updated_at" not in kwargs:
                kwargs = dict(kwargs)
                kwargs["updated_at"] = cls._now()
            return super().update(*args, **kwargs)
        if args and isinstance(args[0], dict):
            first = dict(args[0])
            if "updated_at" not in first:
                first["updated_at"] = cls._now()
            return super().update(first, *args[1:], **kwargs)
        return super().update(*args, **kwargs)

    class Meta:
        database = _database_proxy
        legacy_table_names = False
