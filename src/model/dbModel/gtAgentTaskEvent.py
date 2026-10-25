from __future__ import annotations

import peewee

from constants import TaskStatus

from .base import DbModelBase, EnumField


class GtAgentTaskEvent(DbModelBase):
    """协作任务事件（append-only）。

    每次状态流转 / 评论 / 存量基线快照各记一条；仅追加，不更新、不删除。
    事件角色由 from_status / to_status 的 NULL 组合唯一决定，不落额外的类型字段。
    """

    task_id:     int                = peewee.IntegerField()
    actor_id:    int                = peewee.IntegerField()
    from_status: TaskStatus | None  = EnumField(TaskStatus, null=True)
    to_status:   TaskStatus | None  = EnumField(TaskStatus, null=True)
    content:     str                = peewee.TextField(default='')

    class Meta:
        table_name = "agent_task_events"
        indexes = (
            (("task_id", "id"), False),
            (("actor_id",), False),
        )
