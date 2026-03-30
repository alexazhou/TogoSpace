from __future__ import annotations

from datetime import datetime
from types import MethodType
from typing import Any, Mapping

import peewee


class AutoTimestampMixin:
    """为 Peewee Model 提供时间字段自动注入能力。"""

    @classmethod
    def _now(cls) -> datetime:
        return datetime.now()

    @classmethod
    def _has_timestamp_key(cls, payload: Mapping[Any, Any], field_name: str) -> bool:
        """判断 payload 是否已包含指定时间字段（兼容 str/Field 两种 key）。"""
        for key in payload.keys():
            if isinstance(key, str) and key == field_name:
                return True
            if getattr(key, "name", None) == field_name:
                return True
        return False

    @classmethod
    def _uses_field_keys(cls, payload: Mapping[Any, Any]) -> bool:
        """检测 payload 是否采用了 Peewee 字段对象作为 key。"""
        return any(isinstance(key, peewee.Field) for key in payload.keys())

    @classmethod
    def _inject_insert_timestamps(cls, payload: dict) -> dict:
        """为 insert 补齐 created_at/updated_at（显式传入时不覆盖）。"""
        now = cls._now()
        use_field_keys = cls._uses_field_keys(payload)
        if not cls._has_timestamp_key(payload, "created_at"):
            payload[cls.created_at if use_field_keys else "created_at"] = now
        if not cls._has_timestamp_key(payload, "updated_at"):
            payload[cls.updated_at if use_field_keys else "updated_at"] = now
        return payload

    @classmethod
    def _inject_updated_at(cls, payload: dict, use_field_keys: bool | None = None) -> dict:
        """为 update/upsert 的更新字典补齐 updated_at（显式传入时不覆盖）。"""
        if use_field_keys is None:
            use_field_keys = cls._uses_field_keys(payload)
        if not cls._has_timestamp_key(payload, "updated_at"):
            payload[cls.updated_at if use_field_keys else "updated_at"] = cls._now()
        return payload

    @classmethod
    def _patch_insert_query_for_conflict_timestamp(cls, query):
        """给 insert query 打补丁：on_conflict(update=...) 自动注入 updated_at。

        Peewee 的链式 API 会通过 clone 产生新 query，因此这里同时 patch clone，
        确保 .returning().on_conflict(...) 这类链路仍保留自动注入能力。
        """
        if getattr(query, "_gt_conflict_ts_patched", False):
            return query

        setattr(query, "_gt_conflict_ts_patched", True)
        original_on_conflict = query.on_conflict
        original_clone = query.clone

        def on_conflict_with_updated_at(self, *args, **kwargs):
            if kwargs and isinstance(kwargs.get("update"), Mapping):
                kwargs = dict(kwargs)
                # on_conflict 的 update 常用 Field key，强制按 Field key 注入。
                kwargs["update"] = cls._inject_updated_at(
                    dict(kwargs["update"]),
                    use_field_keys=True,
                )
            return original_on_conflict(*args, **kwargs)

        def clone_with_patch(self, *args, **kwargs):
            cloned = original_clone(*args, **kwargs)
            return cls._patch_insert_query_for_conflict_timestamp(cloned)

        query.on_conflict = MethodType(on_conflict_with_updated_at, query)
        query.clone = MethodType(clone_with_patch, query)
        return query

    @classmethod
    def insert(cls, *args, **kwargs):
        if kwargs:
            kwargs = cls._inject_insert_timestamps(dict(kwargs))
            query = super().insert(*args, **kwargs)
            return cls._patch_insert_query_for_conflict_timestamp(query)
        if args and isinstance(args[0], dict):
            first = cls._inject_insert_timestamps(dict(args[0]))
            query = super().insert(first, *args[1:], **kwargs)
            return cls._patch_insert_query_for_conflict_timestamp(query)
        query = super().insert(*args, **kwargs)
        return cls._patch_insert_query_for_conflict_timestamp(query)

    @classmethod
    def insert_many(cls, rows, fields=None):
        rows = [
            cls._inject_insert_timestamps(dict(row)) if isinstance(row, dict) else row
            for row in rows
        ]
        query = super().insert_many(rows, fields=fields)
        return cls._patch_insert_query_for_conflict_timestamp(query)

    @classmethod
    def update(cls, *args, **kwargs):
        if kwargs:
            kwargs = cls._inject_updated_at(dict(kwargs), use_field_keys=False)
            return super().update(*args, **kwargs)
        if args and isinstance(args[0], dict):
            first = cls._inject_updated_at(dict(args[0]))
            return super().update(first, *args[1:], **kwargs)
        return super().update(*args, **kwargs)
