from pydantic import BaseModel
from typing import List
from datetime import datetime


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
    team_id: int
    team_name: str
    sender: str
    content: str
    time: datetime
