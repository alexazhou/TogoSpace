from __future__ import annotations

from peewee import fn

from constants import SpecialAgent, TaskStatus
from model.dbModel.gtAgentTaskEvent import GtAgentTaskEvent

# 「不是认领事件」：认领 = 系统在 `TODO → IN_PROGRESS` 上写的自动置位说明
# （唯一写点：`taskService.claim_task`）。它内容固定、没有信息量，却总是任务的
# 最新一条，会把真正的人写说明挤出「最近事件」槽位，因此取事件时按本条件排除。
#
# 用 COALESCE 把 NULL 折成空串：直接判 `from_status != 'TODO'` 遇 NULL 仍得 NULL，
# 整个 `~(…)` 会跟着变成 NULL 而误删（0015 结转的系统快照正是 `from=NULL, to=IN_PROGRESS`）。
_NOT_CLAIM_EVENT = ~(
    (GtAgentTaskEvent.actor_id == SpecialAgent.SYSTEM.value)
    & (fn.COALESCE(GtAgentTaskEvent.from_status, "") == TaskStatus.TODO.name)
    & (fn.COALESCE(GtAgentTaskEvent.to_status, "") == TaskStatus.IN_PROGRESS.name)
)


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

    另守 PIN-1（defense-in-depth）：流转事件必须 `from != to`（自环应记为评论、
    `from`/`to` 均空），故 `(非NULL, 非NULL) 且 from == to` 亦为非法——落库前拒绝。

    **不自带事务**：需要与任务主表同生共死时，由调用方用
    `model.dbModel.base.atomic()` 包住，本函数只做裸插入（会跟随外层事务）。
    """
    if from_status is not None and to_status is None:
        raise ValueError("非法的状态组合：from_status 非空而 to_status 为空（禁止「非NULL→NULL」）")
    if from_status is not None and from_status == to_status:
        raise ValueError("非法的状态组合：from_status == to_status（自环不得记为流转事件；应以评论 from/to 均空记录）")
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
    exclude_claim: bool = False,
) -> list[GtAgentTaskEvent]:
    """按自增 id 游标分页读取任务事件（时间线）。

    - 按 id 降序返回（最新在前）；
    - before_id 不为空时只取 id < before_id 的更早事件（向前翻页），不使用 offset；
    - exclude_claim 为 True 时跳过认领事件（见 `_NOT_CLAIM_EVENT`）。默认 False，
      时间线（Web / `list_task_events`）要看全量，只有「注入 Agent 通知」这类
      取最新一条的场景才需要排除噪声。
    """
    query = GtAgentTaskEvent.select().where(GtAgentTaskEvent.task_id == task_id)
    if before_id is not None:
        query = query.where(GtAgentTaskEvent.id < before_id)
    if exclude_claim:
        query = query.where(_NOT_CLAIM_EVENT)
    query = query.order_by(GtAgentTaskEvent.id.desc()).limit(limit)
    return list(await query.aio_execute())


async def get_event(event_id: int) -> GtAgentTaskEvent | None:
    """按主键查询单条事件（append-only 表，只读）。"""
    return await GtAgentTaskEvent.aio_get_or_none(GtAgentTaskEvent.id == event_id)


async def get_latest_event_with_content_by_to_status(
    task_id: int, to_status: TaskStatus
) -> GtAgentTaskEvent | None:
    """取「最近一条**流转到** `to_status` 且 `content` 非空」的事件；没有则 None。

    与 `list_events` 的区别：**不受调用方给的 `limit` 窗口限制**，直接按
    `task_id + to_status + content 非空` 在库里取 id 最大的一条。

    B4 的存在理由：`current_block_reason`（PIN-4「当前搁置原因」）原本从
    「本次查询返回的事件窗口」里找最后一条 `to_status=ON_HOLD`，而工具
    `get_task_detail` 默认 `event_limit=3`。搁置后再产生 3 条以上事件，原因就被
    挤出窗口 ⇒ 读成 `None`，与「从未搁置」不可区分，Agent 会据此以为可以推进。
    窗口大小是「展示口径」，不该决定「当前搁置原因」这种**状态派生值**——故改成独立查询。

    `content` 非空的条件与 `last_note` 口径一致（都是「跳过没写说明的流转事件」）。
    **已知边界**：「恢复 → 再次搁置但没写原因」会回溯到上一次搁置的原因（登记，不用例覆盖
    「猜原因」，因为空 content 本身无法区分「忘了写」与「就是旧原因」）。
    """
    return await (
        GtAgentTaskEvent
        .select()
        .where(
            (GtAgentTaskEvent.task_id == task_id)
            & (GtAgentTaskEvent.to_status == to_status)
            & (fn.COALESCE(GtAgentTaskEvent.content, "") != "")
        )
        .order_by(GtAgentTaskEvent.id.desc())
        .limit(1)
        .aio_first()
    )


async def count_events(task_id: int) -> int:
    """统计某任务的事件**总数**（跨页；不受 `limit` / `before_id` 影响）。"""
    return await (
        GtAgentTaskEvent
        .select()
        .where(GtAgentTaskEvent.task_id == task_id)
        .aio_count()
    )
