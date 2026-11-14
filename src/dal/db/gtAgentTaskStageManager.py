"""任务工作流节点（`agent_task_stages`）的 DAL。

**只做存取**：节点类型保护（REVIEW 不可删）、权限、游标推进等业务判断一律在
`taskService`。这里的每个方法都假定调用方已在同一事务内（见 `_transition_with_event`
的写法），本模块不自行开事务。
"""

from __future__ import annotations

import datetime

import peewee

from constants import StageNodeType, StageStatus
from model.dbModel.gtAgentTaskStage import GtAgentTaskStage


async def create_stages(task_id: int, stages: list[dict]) -> list[GtAgentTaskStage]:
    """按顺序批量创建初始节点链（`stages[0]` 的 `stage_index` = 0）。

    每项形如 `{owner_id, node_type, name?}`；`node_type` 缺省为 `REVIEW`
    （「只给 owner_id 整数」的简写口径，见 step1 §5.1）。
    """
    rows = [
        {
            "task_id": task_id,
            "stage_index": index,
            "node_type": StageNodeType.value_of(spec.get("node_type")) or StageNodeType.REVIEW,
            "name": spec.get("name") or "",
            "owner_id": spec["owner_id"],
            "stage_status": spec.get("stage_status") or StageStatus.WAITING,
        }
        for index, spec in enumerate(stages)
    ]
    if not rows:
        return []
    await GtAgentTaskStage.insert_many(rows).aio_execute()
    return await get_stages_by_task(task_id)


async def get_stages_by_task(task_id: int) -> list[GtAgentTaskStage]:
    """按 `stage_index` 升序返回某任务的全部节点。"""
    return list(
        await GtAgentTaskStage
        .select()
        .where(GtAgentTaskStage.task_id == task_id)
        .order_by(GtAgentTaskStage.stage_index.asc())  # type: ignore[attr-defined]
        .aio_execute()
    )


async def get_stages_by_task_ids(task_ids: list[int]) -> dict[int, list[GtAgentTaskStage]]:
    """批量查询多个任务的节点链（避免 `list_tasks` 出现 N+1）。

    返回 `{task_id: [stage, ...]}`，每组按 `stage_index` 升序；没有节点的 task_id
    不出现在结果里（调用方用 `dict.get(task_id, [])` 兜底）。
    """
    if not task_ids:
        return {}
    rows = await (
        GtAgentTaskStage
        .select()
        .where(GtAgentTaskStage.task_id.in_(task_ids))  # type: ignore[attr-defined]
        .order_by(
            GtAgentTaskStage.task_id.asc(),  # type: ignore[attr-defined]
            GtAgentTaskStage.stage_index.asc(),  # type: ignore[attr-defined]
        )
        .aio_execute()
    )
    grouped: dict[int, list[GtAgentTaskStage]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, []).append(row)
    return grouped


async def get_stage(task_id: int, stage_index: int) -> GtAgentTaskStage | None:
    """取单个节点；不存在返回 None。"""
    return await GtAgentTaskStage.aio_get_or_none(
        (GtAgentTaskStage.task_id == task_id)
        & (GtAgentTaskStage.stage_index == stage_index)
    )


async def count_stages(task_id: int) -> int:
    """当前节点数（用于 `MAX_TASK_STAGES` 上限校验与「是否末节点」判断）。"""
    return await (
        GtAgentTaskStage
        .select()
        .where(GtAgentTaskStage.task_id == task_id)
        .aio_count()
    )


async def update_stage_statuses(
    task_id: int, updates: dict[int, StageStatus]
) -> None:
    """按 `(task_id, stage_index)` 批量更新局部状态。

    逐条 UPDATE：节点数上限只有 `MAX_TASK_STAGES`（20），一次事务内至多 20 条语句，
    比拼一条 `CASE WHEN` 更好读，也不会因为一处 `stage_index` 拼错而静默改错行。
    """
    if not updates:
        return
    now = datetime.datetime.now()
    for stage_index, status in updates.items():
        await (
            GtAgentTaskStage
            .update(stage_status=status, updated_at=now)
            .where(
                (GtAgentTaskStage.task_id == task_id)
                & (GtAgentTaskStage.stage_index == stage_index)
            )
            .aio_execute()
        )


async def update_stage_owner(task_id: int, stage_index: int, new_owner_id: int) -> None:
    """更换节点负责人。"""
    await (
        GtAgentTaskStage
        .update(owner_id=new_owner_id, updated_at=datetime.datetime.now())
        .where(
            (GtAgentTaskStage.task_id == task_id)
            & (GtAgentTaskStage.stage_index == stage_index)
        )
        .aio_execute()
    )


async def insert_stages_after(
    task_id: int, after_index: int, new_stages: list[dict]
) -> list[GtAgentTaskStage]:
    """在 `after_index` 之后插入若干节点，并顺移后续节点的 `stage_index`。

    顺序很重要：**先把 `> after_index` 的节点按 `stage_index` 从大到小整体平移
    `len(new_stages)`**，再插入新节点。反过来（先插后移）会与
    `(task_id, stage_index)` 唯一索引撞车。
    """
    if not new_stages:
        return await get_stages_by_task(task_id)
    shift = len(new_stages)
    existing = await get_stages_by_task(task_id)
    tail = [s for s in existing if s.stage_index > after_index]
    now = datetime.datetime.now()
    for stage in sorted(tail, key=lambda s: s.stage_index, reverse=True):
        await (
            GtAgentTaskStage
            .update(stage_index=stage.stage_index + shift, updated_at=now)
            .where(GtAgentTaskStage.id == stage.id)
            .aio_execute()
        )
    rows = [
        {
            "task_id": task_id,
            "stage_index": after_index + 1 + offset,
            "node_type": StageNodeType.value_of(spec.get("node_type")) or StageNodeType.REVIEW,
            "name": spec.get("name") or "",
            "owner_id": spec["owner_id"],
            "stage_status": StageStatus.WAITING,
        }
        for offset, spec in enumerate(new_stages)
    ]
    await GtAgentTaskStage.insert_many(rows).aio_execute()
    return await get_stages_by_task(task_id)


async def delete_stage_and_reindex(task_id: int, stage_index: int) -> None:
    """删除指定节点，并把 `> stage_index` 的节点序号减 1（保持连续）。

    与 `insert_stages_after` 对称：**从小到大**更新（先 3→2，再 4→3），否则会与
    唯一索引撞车。
    """
    await (
        GtAgentTaskStage
        .delete()
        .where(
            (GtAgentTaskStage.task_id == task_id)
            & (GtAgentTaskStage.stage_index == stage_index)
        )
        .aio_execute()
    )
    tail = [
        s for s in await get_stages_by_task(task_id) if s.stage_index > stage_index
    ]
    now = datetime.datetime.now()
    for stage in sorted(tail, key=lambda s: s.stage_index):
        await (
            GtAgentTaskStage
            .update(stage_index=stage.stage_index - 1, updated_at=now)
            .where(GtAgentTaskStage.id == stage.id)
            .aio_execute()
        )


async def get_task_ids_by_upcoming_owner(owner_id: int) -> list[int]:
    """「后续节点包含我」的任务 id 列表：`owner_id` 且 `stage_status='WAITING'`。

    配合主表 `current_owner_id == me` 做 OR 合并，服务 `list_tasks(include_upcoming=True)`。
    """
    rows = await (
        GtAgentTaskStage
        .select(GtAgentTaskStage.task_id)
        .where(
            (GtAgentTaskStage.owner_id == owner_id)
            & (GtAgentTaskStage.stage_status == StageStatus.WAITING)
        )
        .aio_execute()
    )
    # 保持稳定顺序（去重后升序），便于断言与分页可复现
    return sorted({row.task_id for row in rows})


async def delete_stages_by_task_ids(task_ids: list[int]) -> int:
    """删除若干任务的节点（团队删除 / 测试清理用）。"""
    if not task_ids:
        return 0
    return await (
        GtAgentTaskStage
        .delete()
        .where(GtAgentTaskStage.task_id.in_(task_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )


async def delete_stages_by_team(team_id: int) -> int:
    """删除某团队下所有任务的节点（`delete_tasks_by_team` 的配套清理，避免孤儿节点）。"""
    from model.dbModel.gtAgentTask import GtAgentTask

    subquery = GtAgentTask.select(GtAgentTask.id).where(GtAgentTask.team_id == team_id)
    return await (
        GtAgentTaskStage
        .delete()
        .where(GtAgentTaskStage.task_id.in_(subquery))  # type: ignore[attr-defined]
        .aio_execute()
    )
