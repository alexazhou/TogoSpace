"""`agent_containers` 的数据访问层。

只记录 `create_container` 创建的自建容器；外部 / 他人容器不入库。

**不自带事务**：涉及「预记录 + docker create + 回填」的编排由 `containerService`
负责加锁与补偿清理，本模块只做单表读写。
"""
from __future__ import annotations

import peewee

from model.dbModel.gtAgentContainer import GtAgentContainer


async def create(
    agent_id: int,
    container_name: str,
    image: str,
) -> GtAgentContainer:
    """插入一条预记录（`container_id = NULL`），返回带主键的对象。

    预记录同时占用配额，避免并发创建绕过上限。

    注意：容器名依赖自增主键（`agent_containers.id`），调用方若需要按 ID 命名，
    应在同一事务内先用占位名插入，拿到 `id` 后再调用 `set_container_name` 回填。
    """
    container = GtAgentContainer(
        agent_id=agent_id,
        container_id=None,
        container_name=container_name,
        image=image,
        deleted=0,
    )
    await container.aio_save(force_insert=True)
    return container


async def set_container_name(container: GtAgentContainer, container_name: str) -> GtAgentContainer:
    """回填容器名：插入预记录时还不知道自增 ID，名称要等 ID 出来后才能确定。"""
    container.container_name = container_name
    await container.aio_save(only=[GtAgentContainer.container_name, GtAgentContainer.updated_at])
    return container


async def set_container_id(container: GtAgentContainer, container_id: str) -> GtAgentContainer:
    """回填 12 位短 ID。

    依赖 `idx_agent_containers_container_id` 唯一索引：冲突时抛 `IntegrityError`，
    由调用方补偿清理（本次新建容器 + 本条预记录）。
    """
    container.container_id = container_id
    await container.aio_save(only=[GtAgentContainer.container_id, GtAgentContainer.updated_at])
    return container


async def hard_delete(container: GtAgentContainer) -> None:
    """物理删除记录：仅用于创建流程失败后的清理，不留下残留数据。"""
    if container.id is None:
        return
    await GtAgentContainer.delete().where(GtAgentContainer.id == container.id).aio_execute()


async def mark_deleted(container: GtAgentContainer) -> bool:
    """软删除：`deleted = 1`，保留行用于链路排查与执行回溯。"""
    removed = await (
        GtAgentContainer.update(deleted=1)
        .where(
            (GtAgentContainer.id == container.id)
            & (GtAgentContainer.deleted == 0)
        )
        .aio_execute()
    )
    container.deleted = 1
    return bool(removed)


async def list_active_by_agent(agent_id: int) -> list[GtAgentContainer]:
    """该 Agent 的全部活跃记录（含创建中的预记录），按 id 升序。"""
    return list(
        await GtAgentContainer.select()
        .where(
            (GtAgentContainer.agent_id == agent_id)
            & (GtAgentContainer.deleted == 0)
        )
        .order_by(GtAgentContainer.id)
        .aio_execute()
    )


async def list_active_by_agents(agent_ids: list[int]) -> list[GtAgentContainer]:
    """批量查询多个 Agent 的活跃记录（Agent 列表页容器数统计用）。"""
    if not agent_ids:
        return []
    return list(
        await GtAgentContainer.select()
        .where(
            GtAgentContainer.agent_id.in_(list(agent_ids))  # type: ignore[attr-defined]
            & (GtAgentContainer.deleted == 0)
        )
        .aio_execute()
    )


async def list_active_by_team_agents(agent_ids: list[int]) -> list[GtAgentContainer]:
    """Team 删除 / 改名时按该 Team 的 Agent ID 集合取全部活跃记录。"""
    return await list_active_by_agents(agent_ids)


async def count_active_by_agent(agent_id: int) -> int:
    """该 Agent 的活跃容器数（配额统计口径：含预记录，不排除已停止）。

    必须用 `aio_count()`：peewee 的 `count()` 是**同步立即执行**（返回 int），
    在 `AioDatabase`（`_allow_sync=False`）下会直接抛 `AssertionError: sync query
    is not allowed`，且 `int` 上也没有 `aio_execute` 可挂。
    """
    return int(
        await GtAgentContainer.select()
        .where(
            (GtAgentContainer.agent_id == agent_id)
            & (GtAgentContainer.deleted == 0)
        )
        .aio_count()
    )


async def count_map_by_agent_ids(agent_ids: list[int]) -> dict[int, int]:
    """批量统计各 Agent 的活跃容器数，返回 {agent_id: count}（缺省视为 0）。"""
    if not agent_ids:
        return {}
    rows = await (
        GtAgentContainer.select(
            GtAgentContainer.agent_id,
            # 必须 `fn.COUNT(字段)`：`AutoField` 上没有 `.count()`（那是 Select 的方法），
            # 写成 `id.count()` 会在构造查询时抛 `AttributeError`。
            peewee.fn.COUNT(GtAgentContainer.id).alias("cnt"),
        )
        .where(
            GtAgentContainer.agent_id.in_(list(agent_ids))  # type: ignore[attr-defined]
            & (GtAgentContainer.deleted == 0)
        )
        .group_by(GtAgentContainer.agent_id)
        .aio_execute()
    )
    return {int(row.agent_id): int(row.cnt) for row in rows}


async def get_active_by_id(record_id: int) -> GtAgentContainer | None:
    """按主键取活跃记录（对外的 `record_id`）。"""
    return await GtAgentContainer.aio_get_or_none(
        (GtAgentContainer.id == record_id) & (GtAgentContainer.deleted == 0)
    )


async def get_active_by_container_id(agent_id: int, container_id: str) -> GtAgentContainer | None:
    """按 `agent_id + container_id` 精确取活跃记录。

    必须带 `agent_id`：`container_id` 唯一索引只保证全局不重复，
    但语义上「是不是自己的容器」还得由 agent 维度确认。
    """
    return await GtAgentContainer.aio_get_or_none(
        (GtAgentContainer.agent_id == agent_id)
        & (GtAgentContainer.container_id == container_id)
        & (GtAgentContainer.deleted == 0)
    )


async def get_active_by_name(agent_id: int, container_name: str) -> GtAgentContainer | None:
    """按 `agent_id + container_name` 取活跃记录（预记录按名清理前查归属用）。"""
    return await GtAgentContainer.aio_get_or_none(
        (GtAgentContainer.agent_id == agent_id)
        & (GtAgentContainer.container_name == container_name)
        & (GtAgentContainer.deleted == 0)
    )
