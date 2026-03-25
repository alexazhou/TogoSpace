from pydantic import BaseModel, field_serializer
from typing import List, Optional
from datetime import datetime
from constants import AgentStatus


class TeamInfo(BaseModel):
    name: str
    working_directory: str
    max_function_calls: Optional[int]
    enabled: int
    created_at: str
    updated_at: str


class TeamRoomInfo(BaseModel):
    name: str
    type: str
    initial_topic: Optional[str]
    max_turns: int


class AgentInfo(BaseModel):
    name: str
    template_name: str | None = None
    model: str
    team_name: str
    status: AgentStatus

    @field_serializer('status')
    def serialize_status(self, status: AgentStatus) -> str:
        return status.name


class RoomInfo(BaseModel):
    room_id: int     # 数据库主键 ID
    room_key: str    # room@team 格式
    room_name: str
    team_name: str
    room_type: str   # "private" | "group"
    state: str       # "scheduling" | "idle"
    members: List[str]


class MessageInfo(BaseModel):
    sender: str
    content: str
    time: datetime


class RoomMessagesResponse(BaseModel):
    room_id: int
    room_key: str
    room_name: str
    team_name: str
    messages: List[MessageInfo]


class WsEvent(BaseModel):
    event: str         # 固定为 "message"
    room_id: int
    room_key: str
    room_name: str
    team_name: str
    sender: str
    content: str
    time: datetime
