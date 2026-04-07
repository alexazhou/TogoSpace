from __future__ import annotations

import json
from typing import Any

import peewee
from util import llmApiUtil

from constants import AgentHistoryTag, AgentHistoryStatus, OpenaiApiRole

from .base import DbModelBase, EnumField, EnumListField, JsonField, JsonFieldWithClass
from .historyUsage import HistoryUsage


class GtAgentHistory(DbModelBase):
    agent_id: int = peewee.IntegerField()
    seq: int = peewee.IntegerField(null=False)
    message_json: dict[str, Any] = JsonField(null=False)
    status: AgentHistoryStatus = EnumField(AgentHistoryStatus, null=False, default=AgentHistoryStatus.INIT)
    error_message: str | None = peewee.TextField(null=True)
    tags: list[AgentHistoryTag] = EnumListField(AgentHistoryTag, default=list)
    usage: HistoryUsage | None = JsonFieldWithClass(HistoryUsage, null=True)

    class Meta:
        table_name = "agent_histories"
        indexes = (
            (("agent_id", "seq"), True),
        )

    @classmethod
    def build(
        cls,
        message: llmApiUtil.OpenAIMessage,
        *,
        status: AgentHistoryStatus | None = None,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
        usage: HistoryUsage | None = None,
    ) -> "GtAgentHistory":
        """构建 GtAgentHistory 对象。

        agent_id 和 seq 由 Store 层填充。

        自动推断规则：
        - status: 若未指定，默认 SUCCESS
        - tags: 若未指定，默认空列表
        """
        return cls(
            message_json=message.model_dump(mode="json", exclude_none=True),
            status=status or AgentHistoryStatus.SUCCESS,
            error_message=error_message,
            tags=[] if tags is None else list(tags),
            usage=usage,
        )

    @property
    def openai_message(self) -> llmApiUtil.OpenAIMessage:
        return llmApiUtil.OpenAIMessage.model_validate(self.message_json)

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

    @staticmethod
    def is_tool_call_succeeded(result_json: str | None) -> bool:
        try:
            data = json.loads(result_json)
        except Exception:
            return False
        return bool(data.get("success"))

    @staticmethod
    def extract_tool_call_error_message(result_json: str | None) -> str | None:
        try:
            data = json.loads(result_json)
        except Exception:
            return None
        if bool(data.get("success")):
            return None
        message = data.get("message")
        return None if message is None else str(message)