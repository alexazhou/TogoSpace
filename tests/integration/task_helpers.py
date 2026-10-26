"""集成测试用的任务写入辅助。

DAL 已瘦身为「保存原语」；事务边界用公共的 `model.dbModel.base.atomic()`，
事件由 service 构造。测试里需要造「任务 + 事件」时，用这里的辅助函数复现
service 的写法即可，避免每个用例重复拼装事务块。

**注意**：这些辅助只写库、不推送 messageBus——测试断言事件落库，
推送行为由 `tests/unit/service/test_task_service.py` 覆盖。
"""
from __future__ import annotations

from constants import TaskStatus
from dal.db import gtAgentTaskEventManager, gtAgentTaskManager
from model.dbModel.base import atomic
from model.dbModel.base import atomic
from model.dbModel.gtAgentTask import GtAgentTask


async def create_task_with_event(task: GtAgentTask) -> GtAgentTask:
    """插入任务 + 创建事件（同事务），返回含主键的任务对象。"""
    async with atomic():
        await gtAgentTaskManager.save_task(task, force_insert=True)
        await gtAgentTaskEventManager.append_event(
            task.id,
            task.creator_id,
            from_status=None,
            to_status=task.status,
            content='',
        )
    return task


async def transition_task(
    task: GtAgentTask,
    *,
    actor_id: int,
    from_status: TaskStatus,
    to_status: TaskStatus,
    content: str = '',
) -> GtAgentTask:
    """更新主表状态 + 追加流转事件（同事务），返回任务对象。"""
    async with atomic():
        task.status = to_status
        await gtAgentTaskManager.save_task(task, only=[GtAgentTask.status])
        await gtAgentTaskEventManager.append_event(
            task.id,
            actor_id,
            from_status=from_status,
            to_status=to_status,
            content=content,
        )
    return task
