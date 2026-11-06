"""`agent_memories` 的数据访问层。

单 Agent 私有的长期记忆槽位；**所有查询都必须带 `(team_id, agent_id)`**，
不允许只按 `id` 查——那是跨 Agent 探测的入口（`memory_not_found` 的统一口径
就靠这一层保证）。

**不自带事务**：`memoryService` 的「同名防重 + 配额统计 + 写入」需要在一个
事务里完成才不会被并发绕过，事务边界由调用方用 `model.dbModel.base.atomic()` 包。
"""
from __future__ import annotations

import datetime

import peewee

from constants import MemoryStarLevel, MemoryStatus
from model.dbModel.gtAgentMemory import GtAgentMemory

# 列表 / 快照的排序权重：SUMMARY 在最前、TITLE 次之、NONE 最后。
# 与 `gtAgentTaskManager` 的 `_PRIORITY_WEIGHT` 同一个套路：用显式映射生成 Case，
# 枚举新增成员却忘记配权重时会被回归测试直接抓出来，而不是静默排到最后。
_STAR_WEIGHT_MAP: dict[MemoryStarLevel, int] = {
    MemoryStarLevel.SUMMARY: 3,
    MemoryStarLevel.TITLE: 2,
    MemoryStarLevel.NONE: 1,
}

_STAR_WEIGHT = peewee.Case(
    None,
    tuple(
        (GtAgentMemory.star_level == level, weight)
        for level, weight in _STAR_WEIGHT_MAP.items()
    ),
    default=0,
)


async def save_memory(
    memory: GtAgentMemory,
    *,
    force_insert: bool = False,
    only: list | None = None,
) -> GtAgentMemory:
    """保存记忆（全量插入 / 按字段局部更新）。

    `only` 非空时自动刷新 `updated_at`——局部更新走了 `aio_save(only=...)`，
    peewee 不会自己动时间戳。
    """
    if force_insert:
        await memory.aio_save(force_insert=True)
        return memory
    if only:
        memory.updated_at = datetime.datetime.now()
        if GtAgentMemory.updated_at not in only:
            only = list(only) + [GtAgentMemory.updated_at]
        await memory.aio_save(only=only)
        return memory
    await memory.aio_save()
    return memory


async def get_memory(team_id: int, agent_id: int, memory_id: int) -> GtAgentMemory | None:
    """按 `(team_id, agent_id, id)` 取记忆（含已归档）。

    归档记忆也要能读到：`memory_read` 明确支持「刚归档就想反悔」的场景。
    """
    return await GtAgentMemory.aio_get_or_none(
        (GtAgentMemory.team_id == team_id)
        & (GtAgentMemory.agent_id == agent_id)
        & (GtAgentMemory.id == memory_id)
    )


async def get_active_by_title(
    team_id: int,
    agent_id: int,
    title: str,
    *,
    exclude_id: int | None = None,
) -> GtAgentMemory | None:
    """按标题查该 Agent 的活跃记忆（同名防重）。

    - 只查 `ACTIVE`：已归档的同名记忆允许新建同题槽位（step2 §7.2 明写）。
    - `exclude_id` 用于更新场景排除自身。
    """
    query = GtAgentMemory.select().where(
        (GtAgentMemory.team_id == team_id)
        & (GtAgentMemory.agent_id == agent_id)
        & (GtAgentMemory.title == title)
        & (GtAgentMemory.status == MemoryStatus.ACTIVE)
    )
    if exclude_id is not None:
        query = query.where(GtAgentMemory.id != exclude_id)
    return await query.aio_first()


async def list_memories(
    team_id: int,
    agent_id: int,
    *,
    include_archived: bool = False,
) -> list[GtAgentMemory]:
    """该 Agent 的记忆列表：星标优先（SUMMARY > TITLE > NONE），同级按 `updated_at` 倒序。"""
    query = GtAgentMemory.select().where(
        (GtAgentMemory.team_id == team_id)
        & (GtAgentMemory.agent_id == agent_id)
    )
    if not include_archived:
        query = query.where(GtAgentMemory.status == MemoryStatus.ACTIVE)
    return list(
        await query.order_by(
            _STAR_WEIGHT.desc(),
            GtAgentMemory.updated_at.desc(),
            GtAgentMemory.id.desc(),
        ).aio_execute()
    )


async def list_starred_memories(team_id: int, agent_id: int) -> list[GtAgentMemory]:
    """该 Agent 的活跃星标记忆（System Prompt 快照装配用）。

    排序与 `list_memories` 一致：SUMMARY 优先，再按 `updated_at` 倒序
    （step2 §3.1 第 1 条）。
    """
    return list(
        await GtAgentMemory.select()
        .where(
            (GtAgentMemory.team_id == team_id)
            & (GtAgentMemory.agent_id == agent_id)
            & (GtAgentMemory.status == MemoryStatus.ACTIVE)
            & (GtAgentMemory.star_level != MemoryStarLevel.NONE)
        )
        .order_by(
            _STAR_WEIGHT.desc(),
            GtAgentMemory.updated_at.desc(),
            GtAgentMemory.id.desc(),
        )
        .aio_execute()
    )


async def list_active_for_search(team_id: int, agent_id: int) -> list[GtAgentMemory]:
    """检索候选集：该 Agent 全部 `ACTIVE` 记忆（不打分，打分在 service 层按字段权重做）。

    刻意**不在 SQL 里做关键词过滤**：step2 §4.1 的权重规则（title +4 / tags +3 /
    summary +2 / content +1）需要按字段逐一命中计数，SQL 的 `LIKE` 无法表达
    「命中哪个字段计几分」；且单 Agent 记忆量本身很小，全量拉到内存打分更准确。
    """
    return list(
        await GtAgentMemory.select()
        .where(
            (GtAgentMemory.team_id == team_id)
            & (GtAgentMemory.agent_id == agent_id)
            & (GtAgentMemory.status == MemoryStatus.ACTIVE)
        )
        .aio_execute()
    )


async def count_active_starred(
    team_id: int,
    agent_id: int,
    *,
    star_level: MemoryStarLevel | None = None,
    exclude_id: int | None = None,
) -> int:
    """统计该 Agent 的活跃星标数（配额校验用）。

    - `star_level=None`：统计全部星标（TITLE + SUMMARY）；
    - `star_level=SUMMARY`：只统计概要级。
    - `exclude_id` 必须传自身 id：step2 §2.3 明写「除本条记录外」，否则
      `title -> summary` 升级、以及同级别重复保存都会把自己算进去，造成误报。

    归档记忆天然不计入（`status == ACTIVE` 硬条件）。
    """
    query = GtAgentMemory.select().where(
        (GtAgentMemory.team_id == team_id)
        & (GtAgentMemory.agent_id == agent_id)
        & (GtAgentMemory.status == MemoryStatus.ACTIVE)
        & (GtAgentMemory.star_level != MemoryStarLevel.NONE)
    )
    if star_level is not None:
        query = query.where(GtAgentMemory.star_level == star_level)
    if exclude_id is not None:
        query = query.where(GtAgentMemory.id != exclude_id)
    return int(await query.aio_count())


async def delete_memories_by_agent(agent_id: int) -> int:
    """清理单个 Agent 的全部记忆（Clear Agent Data 联动）。"""
    return int(
        await GtAgentMemory.delete().where(GtAgentMemory.agent_id == agent_id).aio_execute()
    )


async def delete_memories_by_team(team_id: int) -> int:
    """清理整个团队的全部记忆（Clear Team Data 联动）。"""
    return int(
        await GtAgentMemory.delete().where(GtAgentMemory.team_id == team_id).aio_execute()
    )
