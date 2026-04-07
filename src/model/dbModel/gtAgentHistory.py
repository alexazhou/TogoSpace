from __future__ import annotations

import json
from typing import Any

import peewee
from util import llmApiUtil

from constants import AgentHistoryTag, AgentHistoryStage, AgentHistoryStatus, OpenaiApiRole

from .base import DbModelBase, EnumField, EnumListField, JsonField, JsonFieldWithClass
from .historyUsage import HistoryUsage


class GtAgentHistory(DbModelBase):
    agent_id: int = peewee.IntegerField()
    seq: int = peewee.IntegerField(null=False)
    message_json: dict[str, Any] = JsonField(null=False)
    stage: AgentHistoryStage = EnumField[AgentHistoryStage](AgentHistoryStage, null=False, default=AgentHistoryStage.INPUT)
    status: AgentHistoryStatus = EnumField[AgentHistoryStatus](AgentHistoryStatus, null=False, default=AgentHistoryStatus.INIT)
    error_message: str | None = peewee.TextField(null=True)
    tags: list[AgentHistoryTag] = EnumListField[AgentHistoryTag](AgentHistoryTag, default=list)
    usage: HistoryUsage | None = JsonFieldWithClass[HistoryUsage](HistoryUsage, null=True)

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
        stage: AgentHistoryStage | None = None,
        status: AgentHistoryStatus | None = None,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> "GtAgentHistory":
        return cls(
            agent_id=agent_id,
            seq=seq,
            message_json=message.model_dump(mode="json", exclude_none=True),
            stage=stage or cls.infer_stage_from_message(message),
            status=status or AgentHistoryStatus.SUCCESS,
            error_message=error_message,
            tags=[] if tags is None else list(tags),
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
    def infer_role_from_stage(stage: AgentHistoryStage) -> OpenaiApiRole:
        if stage == AgentHistoryStage.INPUT:
            return OpenaiApiRole.USER
        if stage == AgentHistoryStage.INFER:
            return OpenaiApiRole.ASSISTANT
        if stage == AgentHistoryStage.TOOL_RESULT:
            return OpenaiApiRole.TOOL
        raise ValueError(f"不支持的 history stage: {stage}")

    @staticmethod
    def infer_stage_from_message(message: llmApiUtil.OpenAIMessage) -> AgentHistoryStage:
        role = OpenaiApiRole.value_of(message.role)
        if role in (OpenaiApiRole.SYSTEM, OpenaiApiRole.USER):
            return AgentHistoryStage.INPUT
        if role == OpenaiApiRole.ASSISTANT:
            return AgentHistoryStage.INFER
        if role == OpenaiApiRole.TOOL:
            return AgentHistoryStage.TOOL_RESULT
        return AgentHistoryStage.INPUT

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
