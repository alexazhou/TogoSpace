from dataclasses import dataclass, field

from .base import DbModelBase


@dataclass
class RoomStateRecord(DbModelBase):
    room_key: str = ""
    agent_read_index: dict[str, int] = field(default_factory=dict)
