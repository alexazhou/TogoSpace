from dataclasses import dataclass

from .base import DbModelBase


@dataclass
class RoomMessageRecord(DbModelBase):
    room_key: str = ""
    team_name: str = ""
    sender_name: str = ""
    content: str = ""
    send_time: str = ""
