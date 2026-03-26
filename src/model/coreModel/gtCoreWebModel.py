from pydantic import BaseModel, field_serializer
from typing import List
from datetime import datetime
from constants import MemberStatus


class GtCoreMemberInfo(BaseModel):
    name: str
    template_name: str | None = None
    model: str
    team_name: str
    status: MemberStatus

    @field_serializer('status')
    def serialize_status(self, status: MemberStatus) -> str:
        return status.name


class GtCoreRoomInfo(BaseModel):
    room_id: int     # 数据库主键 ID
    room_key: str    # room@team 格式
    room_name: str
    team_name: str
    room_type: str   # "private" | "group"
    state: str       # "scheduling" | "idle"
    members: List[str]


class GtCoreMessageInfo(BaseModel):
    sender: str
    content: str
    time: datetime


class GtCoreRoomMessagesResponse(BaseModel):
    room_id: int
    room_key: str
    room_name: str
    team_name: str
    messages: List[GtCoreMessageInfo]


class GtCoreWsEvent(BaseModel):
    event: str         # 固定为 "message"
    room_id: int
    room_key: str
    room_name: str
    team_name: str
    sender: str
    content: str
    time: datetime
