from pydantic import BaseModel
from typing import List
from datetime import datetime


class AgentInfo(BaseModel):
    name: str
    model: str


class RoomInfo(BaseModel):
    room_id: str       # 当前等于 room_name
    room_name: str
    state: str         # "scheduling" | "idle"
    members: List[str]


class MessageInfo(BaseModel):
    sender: str
    content: str
    time: datetime


class RoomMessagesResponse(BaseModel):
    room_id: str
    room_name: str
    messages: List[MessageInfo]


class WsEvent(BaseModel):
    event: str         # 固定为 "message"
    room_id: str
    room_name: str
    sender: str
    content: str
    time: datetime
