from __future__ import annotations

from datetime import datetime

import peewee

from constants import AgentActivityStatus, AgentActivityType

from .base import DbModelBase, EnumField, JsonField


class GtAgentActivity(DbModelBase):
    """Agent 活动记录：独立于消息历史的运行态观测数据。"""
    agent_id: int = peewee.IntegerField(index=True)
    team_id: int = peewee.IntegerField(index=True)
    activity_type: AgentActivityType = EnumField(AgentActivityType, null=False)
    status: AgentActivityStatus = EnumField(AgentActivityStatus, null=False)
    title: str = peewee.CharField()
    detail: str = peewee.TextField(default="")
    error_message: str | None = peewee.TextField(null=True)
    started_at: datetime = peewee.DateTimeField()
    finished_at: datetime | None = peewee.DateTimeField(null=True)
    duration_ms: int | None = peewee.IntegerField(null=True)
    metadata: dict = JsonField(default=dict)

    class Meta:
        table_name = "agent_activities"
        indexes = (
            (("team_id", "id"), False),
            (("agent_id", "id"), False),
        )
