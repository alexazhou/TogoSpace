from __future__ import annotations

import peewee

from constants import AgentJobStatus, AgentJobType

from .base import DbModelBase, EnumField, JsonField


class GtScheduleJob(DbModelBase):
    """Agent 调度任务记录（scheduler 投递的一次执行单元）。"""
    agent_id: int = peewee.IntegerField()
    job_type: AgentJobType = EnumField(AgentJobType, default=AgentJobType.ROOM_MESSAGE)
    task_data: dict = JsonField(default=dict)  # 通用 payload，如 {"room_id": 123}；保留原名（§3.7）
    status: AgentJobStatus = EnumField(AgentJobStatus, default=AgentJobStatus.PENDING)
    error_message: str | None = peewee.TextField(null=True)

    class Meta:
        table_name = "schedule_jobs"
        indexes = (
            (("agent_id", "status"), False),
        )
