from __future__ import annotations

import datetime

import peewee

from constants import TaskPriority, TaskStatus
from model.dbModel.gtAgentTask import GtAgentTask

# `priority` 以字符串（'HIGH'/'NORMAL'/'LOW'）落库，直接对字段排序会退化成字典序
# （NORMAL > LOW > HIGH，HIGH 反而排最后）。用**显式权重映射**定义优先级顺序，
# 并由映射生成 Case 表达式：新增 `TaskPriority` 枚举值却忘记配权重时，
# 守卫测试（`tests/unit/dal/test_gt_agent_task_manager.py`）会直接转红，
# 而不是让新值静默落到 `default=0`、被排到所有已登记优先级之后。
_PRIORITY_WEIGHT_MAP: dict[TaskPriority, int] = {
    TaskPriority.HIGH: 3,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 1,
}

_PRIORITY_WEIGHT = peewee.Case(
    None,
    tuple(
        (GtAgentTask.priority == priority, weight)
        for priority, weight in _PRIORITY_WEIGHT_MAP.items()
    ),
    default=0,
)


async def create_task(task: GtAgentTask) -> GtAgentTask:
    """保存新任务，返回含主键的实例。

    与「创建事件」（from=NULL、to=初始状态、actor=creator）在**同一事务**内写入，
    保证同生共死（E1）。
    """
    from dal.db import gtAgentTaskEventManager

    database = GtAgentTask._meta.database
    async with database.aio_atomic():
        await task.aio_save(force_insert=True)
        await gtAgentTaskEventManager.append_event(
            task.id,
            task.creator_id,
            from_status=None,
            to_status=task.status,
            content='',
        )
    return task


async def update_task_status_with_event(
    task: GtAgentTask,
    *,
    actor_id: int,
    from_status: TaskStatus,
    to_status: TaskStatus,
    content: str = '',
) -> GtAgentTask:
    """在**同一事务**内更新主表状态 + 追加一条流转事件（from≠to、actor=caller）。

    任一写入失败即整体回滚，保证「每次成功流转必且仅有一条流转事件」（E1）。
    """
    from dal.db import gtAgentTaskEventManager

    database = GtAgentTask._meta.database
    async with database.aio_atomic():
        task.status = to_status
        task.updated_at = datetime.datetime.now()
        await task.aio_save(only=[GtAgentTask.status, GtAgentTask.updated_at])
        await gtAgentTaskEventManager.append_event(
            task.id,
            actor_id,
            from_status=from_status,
            to_status=to_status,
            content=content,
        )
    return task


async def get_task(task_id: int) -> GtAgentTask | None:
    """按主键查询任务。"""
    return await GtAgentTask.aio_get_or_none(GtAgentTask.id == task_id)


def _filtered_task_query(
    team_id: int,
    assignee_id: int | None = None,
    reviewer_id: int | None = None,
    status: TaskStatus | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
):
    """构造任务过滤条件（`list_tasks` 与 `count_tasks` **共用同一 where 构造**，

    保证「本页列表」与「跨页总数」永远按同一口径筛选，不会两处走偏）。
    """
    query = GtAgentTask.select().where(GtAgentTask.team_id == team_id)
    if assignee_id is not None:
        query = query.where(GtAgentTask.assignee_id == assignee_id)
    if reviewer_id is not None:
        query = query.where(GtAgentTask.reviewer_id == reviewer_id)
    if status is not None:
        query = query.where(GtAgentTask.status == status)
    if exclude_statuses:
        query = query.where(GtAgentTask.status.not_in(exclude_statuses))
    return query


async def list_tasks(
    team_id: int,
    assignee_id: int | None = None,
    reviewer_id: int | None = None,
    status: TaskStatus | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
    limit: int = 20,
) -> list[GtAgentTask]:
    """按条件查询任务列表，高优先级优先（HIGH → NORMAL → LOW），同级按 id 升序。"""
    query = (
        _filtered_task_query(team_id, assignee_id, reviewer_id, status, exclude_statuses)
        .order_by(_PRIORITY_WEIGHT.desc(), GtAgentTask.id.asc())
        .limit(limit)
    )
    return list(await query.aio_execute())


async def count_tasks(
    team_id: int,
    assignee_id: int | None = None,
    reviewer_id: int | None = None,
    status: TaskStatus | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
) -> int:
    """统计符合条件的任务**总数**（跨页；不受 `limit` 影响，与 `list_tasks` 同一过滤口径）。"""
    return await _filtered_task_query(
        team_id, assignee_id, reviewer_id, status, exclude_statuses
    ).aio_count()


async def list_pending_dependents(team_id: int, dep_task_id: int) -> list[GtAgentTask]:
    """返回 team 内 `status=PENDING` 且 `depends_on` 含 `dep_task_id` 的任务（下游依赖）。"""
    rows = await (
        GtAgentTask
        .select()
        .where((GtAgentTask.team_id == team_id) & (GtAgentTask.status == TaskStatus.PENDING))
        .aio_execute()
    )
    return [t for t in rows if dep_task_id in (t.depends_on or [])]


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
    """获取 Agent 最早的待处理任务（按 ID 升序）。

    包含两类：
    - 自己作为执行人的 TODO / IN_PROGRESS 任务
    - 自己作为验收人的 REVIEWING 任务
    """
    return await (
        GtAgentTask
        .select()
        .where(
            (
                (GtAgentTask.assignee_id == agent_id) &
                (GtAgentTask.status.in_([TaskStatus.TODO, TaskStatus.IN_PROGRESS]))  # type: ignore[attr-defined]
            ) |
            (
                (GtAgentTask.reviewer_id == agent_id) &
                (GtAgentTask.status == TaskStatus.REVIEWING)
            ),
        )
        .order_by(GtAgentTask.id.asc())  # type: ignore[attr-defined]
        .aio_first()
    )


async def delete_tasks_by_team(team_id: int) -> int:
    """删除团队下所有协作任务。"""
    return await (
        GtAgentTask.delete()
        .where(GtAgentTask.team_id == team_id)
        .aio_execute()
    )


async def update_task(task: GtAgentTask, fields: list) -> GtAgentTask:
    """更新指定字段，同时刷新 updated_at。"""
    task.updated_at = datetime.datetime.now()
    if GtAgentTask.updated_at not in fields:
        fields = list(fields) + [GtAgentTask.updated_at]
    await task.aio_save(only=fields)
    return task
