from __future__ import annotations

from constants import TaskStatus
from model.dbModel.gtAgentTaskEvent import GtAgentTaskEvent


async def append_event(
    task_id: int,
    actor_id: int,
    *,
    from_status: TaskStatus | None = None,
    to_status: TaskStatus | None = None,
    content: str = '',
) -> GtAgentTaskEvent:
    """追加一条任务事件（append-only，无 UPDATE / DELETE）。

    NULL 组合四格（E4）：(NULL,NULL)=评论、(NULL,非NULL)=创建/快照、
    (非NULL,非NULL)=流转、**(非NULL,NULL) 非法**——落库前拒绝。
    """
    if from_status is not None and to_status is None:
        raise ValueError("非法的状态组合：from_status 非空而 to_status 为空（禁止「非NULL→NULL」）")
    event = GtAgentTaskEvent(
        task_id=task_id,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        content=content,
    )
    await event.aio_save(force_insert=True)
    return event


async def list_events(
    task_id: int,
    *,
    limit: int = 50,
    before_id: int | None = None,
) -> list[GtAgentTaskEvent]:
    """按自增 id 游标分页读取任务事件（时间线）。

    - 按 id 降序返回（最新在前）；
    - before_id 不为空时只取 id < before_id 的更早事件（向前翻页），不使用 offset。
    """
    query = GtAgentTaskEvent.select().where(GtAgentTaskEvent.task_id == task_id)
    if before_id is not None:
        query = query.where(GtAgentTaskEvent.id < before_id)
    query = query.order_by(GtAgentTaskEvent.id.desc()).limit(limit)
    return list(await query.aio_execute())


async def count_events(task_id: int) -> int:
    """统计某任务的事件**总数**（跨页；不受 `limit` / `before_id` 影响）。"""
    return await (
        GtAgentTaskEvent
        .select()
        .where(GtAgentTaskEvent.task_id == task_id)
        .aio_count()
    )
