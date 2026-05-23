from __future__ import annotations

import peewee

from .base import DbModelBase, EnumField, JsonField
from constants import RoomType


class GtRoom(DbModelBase):
    team_id: int = peewee.IntegerField()
    name: str = peewee.CharField()
    type: RoomType = EnumField(RoomType, null=False)
    initial_topic: str = peewee.CharField(null=True)
    max_rounds: int | None = peewee.IntegerField(null=True, default=None, column_name='max_turns')  # 最大轮次；NULL 表示使用当前默认值；<=0 表示不限轮次；>0 表示最多进行 N 轮
    agent_ids: list[int] = JsonField(default=list)
    agent_read_index: dict[str, int] = JsonField(null=True)
    speaker_index: int | None = peewee.IntegerField(null=True, column_name='turn_pos')  # 当前发言位索引，重启后恢复；NULL 表示房间处于 IDLE
    biz_id: str | None = peewee.CharField(null=True)  # 业务标识，如 "DEPT:123"
    tags: list[str] = JsonField(default=list)  # 标签列表
    i18n: dict = JsonField(default=dict)  # 多语言数据，含 display_name/initial_topic

    # API 响应时排除的字段（内部状态和时间戳）
    JSON_EXCLUDE = ["created_at", "updated_at", "agent_read_index", "speaker_index"]

    class Meta:
        table_name = "rooms"
        indexes = (
            (('team_id', 'name'), True),
        )


__all__ = ["GtRoom"]
