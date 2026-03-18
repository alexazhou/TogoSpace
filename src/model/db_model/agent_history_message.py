from dataclasses import dataclass

from .base import DbModelBase


@dataclass
class AgentHistoryMessageRecord(DbModelBase):
    agent_key: str = ""
    seq: int = 0
    message_json: str = ""
