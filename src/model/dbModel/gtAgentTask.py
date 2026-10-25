from __future__ import annotations

import peewee

from constants import TaskPriority, TaskStatus

from .base import DbModelBase, EnumField, JsonField


class GtAgentTask(DbModelBase):
    """协作任务记录（Agent 间结构化任务管理）。"""

    team_id:      int            = peewee.IntegerField()
    title:        str            = peewee.TextField()
    description:  str            = peewee.TextField(default='')
    assignee_id:  int            = peewee.IntegerField()
    creator_id:   int            = peewee.IntegerField()
    reviewer_id:  int | None     = peewee.IntegerField(null=True)
    status:       TaskStatus     = EnumField(TaskStatus, default=TaskStatus.TODO)
    priority:     TaskPriority   = EnumField(TaskPriority, default=TaskPriority.NORMAL)
    parent_id:    int | None     = peewee.IntegerField(null=True)
    depends_on:   list[int]      = JsonField(default=list)   # 依赖的 task_id 列表
    room_id:      int | None     = peewee.IntegerField(null=True)

    class Meta:
        table_name = "agent_tasks"
        indexes = (
            (("team_id", "status"), False),
            (("team_id", "assignee_id"), False),
        )
