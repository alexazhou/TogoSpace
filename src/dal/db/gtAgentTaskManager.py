from __future__ import annotations

import datetime

import peewee

from constants import TaskPriority, TaskStatus
from model.dbModel.gtAgentTask import GtAgentTask

# `list_tasks` 的排序口径（值即标识符，便于日志与测试直接比对）。
# 默认值必须是「优先级优先」——这是 Web 列表接口（`agentController.py`）依赖的既有顺序，
# 倒序只能显式要求，见 `list_tasks` 的 docstring。
ORDER_PRIORITY_FIRST = "priority_first"
ORDER_ID_DESC = "id_desc"

# `priority` 以字符串（'HIGH'/'NORMAL'/'LOW'）落库，直接对字段排序会退化成字典序
# （NORMAL > LOW > HIGH，HIGH 反而排最后）。用**显式权重映射**定义优先级顺序，
# 并由映射生成 Case 表达式：新增 `TaskPriority` 枚举值却忘记配权重时，
# 回归测试（`tests/unit/dal/test_gt_agent_task_manager.py`）会直接转红，
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


async def save_task(
    task: GtAgentTask,
    *,
    force_insert: bool = False,
    only: list | None = None,
) -> GtAgentTask:
    """保存任务（全量插入 / 按字段局部更新）。

    **不自带事务**，需要与事件同生共死时由调用方用
    `model.dbModel.base.atomic()` 包住（跨表公共事务边界）。
    `only` 非空时会自动刷新 `updated_at`。
    """
    if force_insert:
        await task.aio_save(force_insert=True)
        return task
    if only:
        task.updated_at = datetime.datetime.now()
        if GtAgentTask.updated_at not in only:
            only = list(only) + [GtAgentTask.updated_at]
        await task.aio_save(only=only)
        return task
    await task.aio_save()
    return task


async def get_task(task_id: int) -> GtAgentTask | None:
    """按主键查询任务。"""
    return await GtAgentTask.aio_get_or_none(GtAgentTask.id == task_id)


def _filtered_task_query(
    team_id: int,
    assignee_id: int | None = None,
    reviewer_id: int | None = None,
    status: TaskStatus | list[TaskStatus] | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
    agent_ids: list[int] | None = None,
    title_keyword: str | None = None,
):
    """构造任务过滤条件（`list_tasks` 与 `count_tasks` **共用同一 where 构造**，

    保证「本页列表」与「跨页总数」永远按同一口径筛选，不会两处走偏）。

    `agent_ids` 是「可见 Agent 集合」过滤：命中集内**任一**身份即可，
    即 `assignee_id ∈ agent_ids` **OR** `reviewer_id ∈ agent_ids` ——
    主管必须能看到「自己作为验收人」的任务，只按 assignee 过滤会漏。
    与 `assignee_id` / `reviewer_id` 的精确过滤是 **AND** 关系（两者只会在
    「调用方已给出明确过滤目标」以外的场景出现，同时传属于收紧而非放宽）。

    `status` 接受**单值或列表**：列表走 `IN`（任一命中），单值仍走 `==`。
    单值路径刻意不合并成单元素列表，避免既有 SQL 与断言口径被无谓改动。

    `title_keyword` 按**任务标题**做子串匹配（`LIKE '%kw%'`，SQLite 下 ASCII 大小写
    不敏感）。空串由调用方拦掉：`LIKE '%%'` 会匹配全部任务，等于静默取消过滤。
    peewee 的 `contains` 会自动转义 `%` / `_` / `\\`，关键字按字面匹配。
    """
    query = GtAgentTask.select().where(GtAgentTask.team_id == team_id)
    if assignee_id is not None:
        query = query.where(GtAgentTask.assignee_id == assignee_id)
    if reviewer_id is not None:
        query = query.where(GtAgentTask.reviewer_id == reviewer_id)
    if agent_ids is not None:
        query = query.where(
            (GtAgentTask.assignee_id.in_(agent_ids))
            | (GtAgentTask.reviewer_id.in_(agent_ids))
        )
    if isinstance(status, list):
        if status:  # 空列表不能进 IN（peewee 会生成恒假条件，语义上也应由调用方拦掉）
            query = query.where(GtAgentTask.status.in_(status))
    elif status is not None:
        query = query.where(GtAgentTask.status == status)
    if exclude_statuses:
        query = query.where(GtAgentTask.status.not_in(exclude_statuses))
    if title_keyword is not None:
        query = query.where(GtAgentTask.title.contains(title_keyword))
    return query


def _task_ordering(order: str) -> tuple:
    """把排序标识符翻译成 peewee 排序表达式。

    - `ORDER_PRIORITY_FIRST`（默认）：`priority desc, id asc` —— **与新增 `order` 参数前一致**；
    - `ORDER_ID_DESC`：`id desc`（最新在前）。
    非法值抛 `ValueError`：排序写错不该静默兜底成一个「看起来正常」的顺序。
    """
    if order == ORDER_PRIORITY_FIRST:
        return (_PRIORITY_WEIGHT.desc(), GtAgentTask.id.asc())
    if order == ORDER_ID_DESC:
        return (GtAgentTask.id.desc(),)
    raise ValueError(
        f"不支持的排序方式：{order}，有效值为 {ORDER_PRIORITY_FIRST} / {ORDER_ID_DESC}"
    )


async def list_tasks(
    team_id: int,
    assignee_id: int | None = None,
    reviewer_id: int | None = None,
    status: TaskStatus | list[TaskStatus] | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
    limit: int = 20,
    agent_ids: list[int] | None = None,
    title_keyword: str | None = None,
    order: str = ORDER_PRIORITY_FIRST,
) -> list[GtAgentTask]:
    """按条件查询任务列表。

    `order` 决定排序，**默认值与新增该参数前完全一致**——`taskService.list_tasks`
    同时被 Web 列表接口（`agentController.py` 的 Agent / Team 任务列表）使用，
    改默认值会静默翻掉前端顺序，所以倒序只能由调用方显式要求：
    - `ORDER_PRIORITY_FIRST`（默认）：`priority desc, id asc`（HIGH → NORMAL → LOW）；
    - `ORDER_ID_DESC`：`id desc`（最新在前，Agent 侧要看刚发生的事）。
    传其他值一律 `ValueError`，不做静默兜底（写错排序不该看起来正常）。

    `agent_ids` 非空时只返回「assignee 或 reviewer 落在集合内」的任务；
    `None` 表示不加该过滤（行为与新增该参数前完全一致）。
    `status` 可传单值或列表（列表 = 任一命中）。
    `title_keyword` 按任务标题子串匹配，`None` 表示不按标题过滤。
    """
    query = (
        _filtered_task_query(
            team_id, assignee_id, reviewer_id, status, exclude_statuses, agent_ids, title_keyword
        )
        .order_by(*_task_ordering(order))
        .limit(limit)
    )
    return list(await query.aio_execute())


async def count_tasks(
    team_id: int,
    assignee_id: int | None = None,
    reviewer_id: int | None = None,
    status: TaskStatus | list[TaskStatus] | None = None,
    exclude_statuses: list[TaskStatus] | None = None,
    agent_ids: list[int] | None = None,
    title_keyword: str | None = None,
) -> int:
    """统计符合条件的任务**总数**（跨页；不受 `limit` 影响，与 `list_tasks` 同一过滤口径）。"""
    return await _filtered_task_query(
        team_id, assignee_id, reviewer_id, status, exclude_statuses, agent_ids, title_keyword
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
