from __future__ import annotations

import peewee

from .base import DbModelBase


class GtRoomMessage(DbModelBase):
    room_id: int = peewee.IntegerField(null=False)
    agent_id: int = peewee.IntegerField(null=False, default=0)
    content: str = peewee.TextField(null=False)
    send_time: str = peewee.CharField(null=False)
    insert_immediately: bool = peewee.BooleanField(null=False, default=False)
    # V20: 消息在房间内的显示顺序。immediately 消息在注入前为 NULL，注入时由 agentTurnRunner 赋值。
    seq: int | None = peewee.IntegerField(null=True, default=None)

    class Meta:
        table_name = "room_messages"
