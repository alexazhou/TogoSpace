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

    # ---- 任务系统 v3：单棒接力游标（主表冗余，避免列表检索多表计算）----
    # 真值来源是 agent_task_stages；这两个字段是「按当前持棒人查待办」走索引的快照。
    # 迁移 0022 会为存量任务回填（回填后逻辑非空），故这里允许 NULL 以兼容迁移前的行。
    current_stage:    int        = peewee.IntegerField(default=0)
    current_owner_id: int | None = peewee.IntegerField(null=True)

    class Meta:
        table_name = "agent_tasks"
        indexes = (
            (("team_id", "status"), False),
            (("team_id", "assignee_id"), False),
            # v3：list_tasks 默认「只看当前持棒人」的高频查询（team + 持棒人 + 状态）
            (("team_id", "current_owner_id", "status"), False),
        )
