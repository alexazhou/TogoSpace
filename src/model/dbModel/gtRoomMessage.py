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
    # V21: 引用消息 ID，指向同房间内的一条历史消息。NULL 表示无引用。
    quote_id: int | None = peewee.IntegerField(null=True, default=None, index=True)

    # 非数据库字段，不持久化；由业务代码在创建或恢复消息时手动赋值
    sender_display_name: str = ""
    # 非数据库字段：引用消息的摘要信息，发送消息时由业务代码填充
    quote_sender_name: str = ""
    quote_content_preview: str = ""

    def to_json(self) -> dict:
        """序列化，包含非持久化字段（sender_display_name / quote_sender_name / quote_content_preview）。"""
        data = super().to_json()
        data["sender_display_name"] = self.sender_display_name
        data["quote_sender_name"] = self.quote_sender_name
        data["quote_content_preview"] = self.quote_content_preview
        return data

    class Meta:
        table_name = "room_messages"
