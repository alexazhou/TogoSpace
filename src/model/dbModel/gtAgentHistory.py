from __future__ import annotations

from functools import cached_property

import peewee
from util import llmApiUtil

from constants import AgentHistoryTag

from .base import DbModelBase, EnumListField


class GtAgentHistory(DbModelBase):
    agent_id: int = peewee.IntegerField()
    seq: int = peewee.IntegerField(null=False)
    message_json: str = peewee.TextField(null=False)
    tags: list[AgentHistoryTag] = EnumListField[AgentHistoryTag](AgentHistoryTag, default=list)

    class Meta:
        table_name = "agent_histories"
        indexes = (
            (("agent_id", "seq"), True),
        )

    @classmethod
    def from_openai_message(
        cls,
        agent_id: int,
        seq: int,
        message: llmApiUtil.OpenAIMessage,
        tags: list[AgentHistoryTag] | None = None,
    ) -> "GtAgentHistory":
        return cls(
            agent_id=agent_id,
            seq=seq,
            message_json=message.model_dump_json(exclude_none=True),
            tags=[] if tags is None else list(tags),
        )

    @cached_property
    def openai_message(self) -> llmApiUtil.OpenAIMessage:
        return llmApiUtil.OpenAIMessage.model_validate_json(self.message_json)

    @property
    def role(self):
        return self.openai_message.role

    @property
    def content(self):
        return self.openai_message.content

    @property
    def tool_calls(self):
        return self.openai_message.tool_calls

    @property
    def tool_call_id(self):
        return self.openai_message.tool_call_id
