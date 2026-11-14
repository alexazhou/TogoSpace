from __future__ import annotations

import peewee

from constants import StageNodeType, StageStatus

from .base import DbModelBase, EnumField


class GtAgentTaskStage(DbModelBase):
    """任务工作流节点（v3 单棒接力的有序节点链中的一环）。

    一类节点一行，`(task_id, stage_index)` 唯一；`stage_index` 连续从 0 开始
    （删除节点后由 DAL 重排，不留空洞）。**业务校验一律不在这里**——权限、
    节点类型保护、游标推进全部收口在 `taskService`，本模型与 DAL 只做存取。
    """

    task_id:      int             = peewee.IntegerField()
    stage_index:  int             = peewee.IntegerField()
    node_type:    StageNodeType   = EnumField(StageNodeType)
    name:         str             = peewee.TextField(default='')
    owner_id:     int             = peewee.IntegerField()
    stage_status: StageStatus     = EnumField(StageStatus, default=StageStatus.WAITING)

    class Meta:
        table_name = "agent_task_stages"
        indexes = (
            (("task_id", "stage_index"), True),
            (("owner_id", "stage_status"), False),
        )
