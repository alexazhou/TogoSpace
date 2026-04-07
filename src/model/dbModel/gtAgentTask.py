from __future__ import annotations

import peewee

from constants import AgentTaskStatus, AgentTaskType

from .base import DbModelBase, EnumField, JsonField


class GtAgentTask(DbModelBase):
    """Agent 任务记录。"""
    agent_id: int = peewee.IntegerField()
    task_type: AgentTaskType = EnumField(AgentTaskType, default=AgentTaskType.ROOM_MESSAGE)
    task_data: dict = JsonField(default=dict)  # 存储 task 相关信息，如 {"room_id": 123}
    status: AgentTaskStatus = EnumField(AgentTaskStatus, default=AgentTaskStatus.PENDING)
    error_message: str | None = peewee.TextField(null=True)

    class Meta:
        table_name = "agent_tasks"
        indexes = (
            (("agent_id", "status"), False),
        )
