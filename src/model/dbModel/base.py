from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any, Generic, List, TypeVar, cast

import peewee
import peewee_async
from pydantic import BaseModel
from playhouse.shortcuts import model_to_dict
from constants import EnhanceEnum
from model.dbModel.auto_timestamp_mixin import AutoTimestampMixin
from util import jsonUtil

TJson = TypeVar("TJson")
TEnum = TypeVar("TEnum", bound="EnhanceEnum")
TClassJson = TypeVar("TClassJson")
TPydanticModel = TypeVar("TPydanticModel", bound=BaseModel)

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
            logger.warning("JsonField parse failed for field '%s', returning None: value=%r, error=%s", field_name, value, exc)
            return None


class JsonFieldWithClass(peewee.TextField, Generic[TClassJson]):
    """将自定义类对象与 TEXT(JSON) 自动互转。"""

    def __init__(
        self,
        json_cls: type[TClassJson],
        json_config: dict[jsonUtil.JSONConfig, Any] | None = None,
        *args,
        **kwargs,
    ):
        self.json_cls = json_cls
        self.json_config = json_config
        super().__init__(*args, **kwargs)

    def db_value(self, value: TClassJson | None) -> str | None:
        if value is None:
            return None
        return jsonUtil.json_dump(value, config=self.json_config)

    def python_value(self, value) -> TClassJson | None:
        if value is None:
            return None
        if isinstance(value, self.json_cls):
            return value
        try:
            if isinstance(value, str):
                return cast(TClassJson, jsonUtil.json_load(value, self.json_cls, config=self.json_config))
            return cast(TClassJson, jsonUtil.json_data_to_object(value, self.json_cls, config=self.json_config))
        except Exception as exc:
            field_name = getattr(self, "name", None) or "<unknown>"
            logger.warning("JsonFieldWithClass parse failed for field '%s', returning None: value=%r, error=%s", field_name, value, exc)
            return None


class PydanticJsonField(peewee.TextField, Generic[TPydanticModel]):
    """将 Pydantic BaseModel 与 TEXT(JSON) 自动互转。"""

    def __init__(self, model_cls: type[TPydanticModel], *args, **kwargs):
        self.model_cls = model_cls
        super().__init__(*args, **kwargs)

    def db_value(self, value: TPydanticModel | dict[str, Any] | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.model_cls):
            return value.model_dump_json(exclude_none=True)
        validated = self.model_cls.model_validate(value)
        return validated.model_dump_json(exclude_none=True)

    def python_value(self, value) -> TPydanticModel | None:
        if value is None:
            return None
        if isinstance(value, self.model_cls):
            return value
        try:
            if isinstance(value, str):
                return self.model_cls.model_validate_json(value)
            return self.model_cls.model_validate(value)
        except Exception as exc:
            field_name = getattr(self, "name", None) or "<unknown>"
            logger.warning("PydanticJsonField parse failed for field '%s', returning None: value=%r, error=%s", field_name, value, exc)
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


class EnumListField(JsonField[list[TEnum]], Generic[TEnum]):
    """枚举列表字段，用 JSON 数组存储枚举 name，读取时恢复为枚举对象列表。"""

    def __init__(self, enum_cls: type[TEnum], *args, **kwargs):
        self.enum = enum_cls
        super().__init__(*args, **kwargs)

    def db_value(self, value: list[TEnum] | list[str] | None) -> str | None:
        if value is None:
            return None

        names: list[str] = []
        for item in value:
            enum_value = self.enum.value_of(item)
            if enum_value is None:
                raise ValueError(f"invalid enum list item for {self.enum.__name__}: {item!r}")
            names.append(enum_value.name)

        return super().db_value(names)

    def python_value(self, value) -> list[TEnum] | None:
        raw_items = super().python_value(value)
        if raw_items is None:
            return None

        enum_items: list[TEnum] = []
        for item in raw_items:
            enum_value = self.enum.value_of(item)
            if enum_value is None:
                raise ValueError(f"invalid enum list item for {self.enum.__name__}: {item!r}")
            enum_items.append(cast(TEnum, enum_value))

        return enum_items


class DbModelBase(AutoTimestampMixin, peewee_async.AioModel):
    id:         int = peewee.AutoField()
    created_at: datetime = peewee.DateTimeField(default=datetime.now)
    updated_at: datetime = peewee.DateTimeField(default=datetime.now)

    JSON_EXCLUDE: List[str] = []  # 序列化时排除的字段

    class Meta:
        database = _database_proxy
        legacy_table_names = False

    def to_json(self) -> dict:
        """转换为 JSON 可序列化的字典。"""
        return model_to_dict(self, exclude=self.JSON_EXCLUDE)
