from __future__ import annotations

import peewee

from constants import StageStatus, TaskEventAction, TaskStatus

from .base import DbModelBase, EnumField


class GtAgentTaskEvent(DbModelBase):
    """协作任务事件（append-only）。

    每次状态流转 / 评论 / 存量基线快照各记一条；仅追加，不更新、不删除。

    事件角色由 `from_status` / `to_status` 的 NULL 组合唯一决定（v2 口径，保持不变）。
    v3 追加三列：`action_type`（动作类型）、`from_stage` / `to_stage`（节点游标），
    用来消除「同态流转」歧义（`REVIEWING → REVIEWING` 既可能是打回给前一个审核节点，
    也可能是交棒给下一个审核节点，四个状态字段完全一致）。历史事件这三列为 NULL。
    """

    task_id:     int                          = peewee.IntegerField()
    actor_id:    int                          = peewee.IntegerField()
    from_status: TaskStatus | None            = EnumField(TaskStatus, null=True)
    to_status:   TaskStatus | None            = EnumField(TaskStatus, null=True)
    content:     str                          = peewee.TextField(default='')
    action_type: TaskEventAction | None       = EnumField(TaskEventAction, null=True)
    from_stage:  int | None                   = peewee.IntegerField(null=True)
    to_stage:    int | None                   = peewee.IntegerField(null=True)

    class Meta:
        table_name = "agent_task_events"
        indexes = (
            (("task_id", "id"), False),
            (("actor_id",), False),
        )
