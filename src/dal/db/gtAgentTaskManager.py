from __future__ import annotations

import datetime

from constants import TaskStatus
from model.dbModel.gtAgentTask import GtAgentTask


async def create_task(task: GtAgentTask) -> GtAgentTask:
    """保存新任务，返回含主键的实例。"""
    await task.aio_save(force_insert=True)
    return task


async def get_task(task_id: int) -> GtAgentTask | None:
    """按主键查询任务。"""
    return await GtAgentTask.aio_get_or_none(GtAgentTask.id == task_id)


async def list_tasks(
    team_id: int,
    assignee_id: int | None = None,
    manager_id: int | None = None,
    status: TaskStatus | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
    limit: int = 20,
) -> list[GtAgentTask]:
    """按条件查询任务列表，按创建时间降序。"""
    query = GtAgentTask.select().where(GtAgentTask.team_id == team_id)
    if assignee_id is not None:
        query = query.where(GtAgentTask.assignee_id == assignee_id)
    if manager_id is not None:
        query = query.where(GtAgentTask.manager_id == manager_id)
    if status is not None:
        query = query.where(GtAgentTask.status == status)
    if exclude_statuses:
        query = query.where(GtAgentTask.status.not_in(exclude_statuses))
    query = query.order_by(GtAgentTask.id.desc()).limit(limit)
    return list(await query.aio_execute())


async def get_tasks_by_ids(task_ids: list[int]) -> list[GtAgentTask]:
    """批量查询任务（用于依赖状态检查）。"""
    if not task_ids:
        return []
    return list(
        await GtAgentTask.select()
        .where(GtAgentTask.id.in_(task_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )


async def get_first_active_task(agent_id: int) -> GtAgentTask | None:
    """获取 Agent 最早的 TODO 或 IN_PROGRESS 任务（按 ID 升序）。"""
    return await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.assignee_id == agent_id,
            GtAgentTask.status.in_([TaskStatus.TODO, TaskStatus.IN_PROGRESS]),  # type: ignore[attr-defined]
        )
        .order_by(GtAgentTask.id.asc())  # type: ignore[attr-defined]
        .aio_first()
    )


async def update_task(task: GtAgentTask, fields: list) -> GtAgentTask:
    """更新指定字段，同时刷新 updated_at。"""
    task.updated_at = datetime.datetime.now()
    if GtAgentTask.updated_at not in fields:
        fields = list(fields) + [GtAgentTask.updated_at]
    await task.aio_save(only=fields)
    return task
