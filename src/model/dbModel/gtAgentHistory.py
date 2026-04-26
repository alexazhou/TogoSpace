from __future__ import annotations

import json

import peewee
from util import llmApiUtil

from constants import AgentHistoryTag, AgentHistoryStatus, OpenaiApiRole

from .base import DbModelBase, EnumField, EnumListField, JsonFieldWithClass, PydanticJsonField
from .historyUsage import HistoryUsage


class GtAgentHistory(DbModelBase):
    agent_id: int = peewee.IntegerField()
    seq: int = peewee.IntegerField(null=False)
    role: OpenaiApiRole = EnumField(OpenaiApiRole, null=False)
    tool_call_id: str | None = peewee.TextField(null=True)
    message: llmApiUtil.OpenAIMessage | None = PydanticJsonField(llmApiUtil.OpenAIMessage, null=True)
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
        agent_id: int | None = None,
        seq: int | None = None,
    ) -> "GtAgentHistory":
        """构建 GtAgentHistory 对象。

        agent_id 和 seq 可选传入，若未指定则由 Store 层填充。

        自动推断规则：
        - status: 若未指定，默认 SUCCESS
        - tags: 若未指定，默认空列表
        """
        return cls(
            agent_id=agent_id or 0,
            seq=seq or 0,
            role=message.role,
            tool_call_id=message.tool_call_id,
            message=message,
            status=status or AgentHistoryStatus.SUCCESS,
            error_message=error_message,
            tags=[] if tags is None else list(tags),
            usage=usage,
        )

    @property
    def has_message(self) -> bool:
        return self.message is not None

    @classmethod
    def build_placeholder(
        cls,
        *,
        role: OpenaiApiRole,
        tool_call_id: str | None = None,
        status: AgentHistoryStatus = AgentHistoryStatus.INIT,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
        usage: HistoryUsage | None = None,
    ) -> "GtAgentHistory":
        if status == AgentHistoryStatus.SUCCESS:
            raise ValueError("placeholder history item cannot have SUCCESS status")
        if role == OpenaiApiRole.TOOL and not tool_call_id:
            raise ValueError("tool placeholder requires tool_call_id")
        if role != OpenaiApiRole.TOOL and tool_call_id is not None:
            raise ValueError("tool_call_id is only allowed for tool history items")
        return cls(
            role=role,
            tool_call_id=tool_call_id,
            message=None,
            status=status,
            error_message=error_message,
            tags=[] if tags is None else list(tags),
            usage=usage,
        )

    @property
    def openai_message_or_none(self) -> llmApiUtil.OpenAIMessage | None:
        return self.message

    @property
    def openai_message(self) -> llmApiUtil.OpenAIMessage:
        message = self.openai_message_or_none
        if message is None:
            raise ValueError(f"history item {self.id or '<unsaved>'} has no message")
        return message

    @property
    def content(self):
        message = self.openai_message_or_none
        if message is None:
            return None
        return message.content

    @property
    def tool_calls(self):
        message = self.openai_message_or_none
        if message is None:
            return None
        return message.tool_calls

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
