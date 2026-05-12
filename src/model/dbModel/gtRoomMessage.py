from __future__ import annotations

from datetime import datetime

import peewee

from .base import DbModelBase


class GtRoomMessage(DbModelBase):
    room_id: int = peewee.IntegerField(null=False)
    sender_id: int = peewee.IntegerField(null=True)
    content: str = peewee.TextField(null=False)
    send_time: datetime = peewee.DateTimeField(null=False)
    insert_immediately: bool = peewee.BooleanField(null=False, default=False)
    # V20: 消息在房间内的显示顺序。immediately 消息在注入前为 NULL，注入时由 agentTurnRunner 赋值。
    seq: int | None = peewee.IntegerField(null=True, default=None)

    # 非数据库字段，不持久化；由业务代码在创建或恢复消息时手动赋值
    sender_display_name: str = ""

    class Meta:
        table_name = "room_messages"
