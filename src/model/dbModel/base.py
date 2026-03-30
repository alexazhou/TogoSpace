from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Generic, TypeVar, cast

import peewee
import peewee_async
from constants import EnhanceEnum
from model.dbModel.auto_timestamp_mixin import AutoTimestampMixin

TJson = TypeVar("TJson")
TEnum = TypeVar("TEnum", bound="EnhanceEnum")

_database_proxy: peewee.DatabaseProxy = peewee.DatabaseProxy()
logger = logging.getLogger(__name__)


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
        try:
            return cast(TJson, json.loads(value))
        except (TypeError, ValueError) as exc:
            field_name = getattr(self, "name", None) or "<unknown>"
            logger.warning(
                "JsonField parse failed for field '%s', returning None: value=%r, error=%s",
                field_name,
                value,
                exc,
            )
            return None


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


class DbModelBase(AutoTimestampMixin, peewee_async.AioModel):
    id:         int = peewee.AutoField()
    created_at: datetime = peewee.DateTimeField(default=datetime.now)
    updated_at: datetime = peewee.DateTimeField(default=datetime.now)

    class Meta:
        database = _database_proxy
        legacy_table_names = False
