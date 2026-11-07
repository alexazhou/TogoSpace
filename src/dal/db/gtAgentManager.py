from __future__ import annotations

from peewee import fn

from constants import EmployStatus, DriverType
from model.dbModel.gtAgent import GtAgent
from util.cacheUtil import CacheStore


# ─────────────────────────────────────────────────────────────────────────────
# 缓存层
# ─────────────────────────────────────────────────────────────────────────────

# Agent 缓存：key 为 agent_id，value 为 GtAgent 对象
_agent_cache = CacheStore[int, GtAgent](key_extractor=lambda a: a.id)


def clear_agent_cache() -> None:
    """清空全部 agent 缓存（换库 / 测试隔离）。

    `CacheStore.clear()` 会推进版本号，换库前发出的在途查询（包括从未单独
    失效过的 id）都不会再回填。
    """
    _agent_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 查询方法（按 team）
# ─────────────────────────────────────────────────────────────────────────────


async def get_agent(team_id: int, name: str, status: EmployStatus | None = EmployStatus.ON_BOARD) -> GtAgent | None:
    """按 team + name 查询单个成员，支持跨团队 Agent。

    Args:
        team_id: 团队 ID
        name: Agent 名称
        status: 状态过滤，默认 ON_BOARD；传入 None 表示不限状态

    Note: 与 get_team_agents_by_names 不同，此方法返回单个 GtAgent 对象而非列表。
    """
    conditions = [(GtAgent.team_id == team_id) | (GtAgent.team_id == -1), GtAgent.name == name]
    if status is not None:
        conditions.append(GtAgent.employ_status == status)
    return await GtAgent.aio_get_or_none(*conditions)


async def get_team_all_agents(team_id: int, status: EmployStatus | None = None, include_cross_team: bool = False) -> list[GtAgent]:
    """按 team_id 查询全部成员，可选按 employ_status 过滤。

    Args:
        team_id: 团队 ID
        status: 可选状态过滤，None 表示不过滤（返回所有状态）
        include_cross_team: True 时包含 team_id=-1 的跨团队 Agent（如 SpecialAgent）
    """
    if include_cross_team:
        query = GtAgent.select().where((GtAgent.team_id == team_id) | (GtAgent.team_id == -1))
    else:
        query = GtAgent.select().where(GtAgent.team_id == team_id)
    if status is not None:
        query = query.where(GtAgent.employ_status == status)
    return list(await query.order_by(GtAgent.name).aio_execute())


async def get_team_agents_by_ids(team_id: int, agent_ids: list[int]) -> list[GtAgent]:
    """按 team_id + agent_ids 批量查询成员，保持原始顺序。

    Args:
        team_id: 团队 ID
        agent_ids: Agent ID 列表

    Note: 同时查询 team_id 匹配和 team_id=-1（跨团队）的记录。
    """
    if not agent_ids:
        return []

    gt_agents = list(
        await GtAgent.select()
        .where(
            GtAgent.id.in_(agent_ids),  # type: ignore[attr-defined]
            (GtAgent.team_id == team_id) | (GtAgent.team_id == -1),
        )
        .aio_execute()
    )
    agent_map = {agent.id: agent for agent in gt_agents}

    # 保持原始顺序
    agents: list[GtAgent] = []
    for agent_id in agent_ids:
        agent = agent_map.get(agent_id)
        if agent is not None:
            agents.append(agent)
    return agents


async def get_team_agents_by_names(team_id: int, names: list[str]) -> list[GtAgent]:
    """按 team_id + names 批量查询成员，保持原始顺序。

    Args:
        team_id: 团队 ID
        names: Agent 名称列表

    Note:
        - 同时查询 team_id 匹配和 team_id=-1（跨团队）的记录。
        - 与 get_agent 不同，此方法返回列表，适合批量查询场景。
    """
    if not names:
        return []

    gt_agents = list(
        await GtAgent.select()
        .where(
            GtAgent.name.in_(names),  # type: ignore[attr-defined]
            (GtAgent.team_id == team_id) | (GtAgent.team_id == -1),
        )
        .aio_execute()
    )
    name_to_agent = {agent.name: agent for agent in gt_agents}

    # 保持原始顺序
    agents: list[GtAgent] = []
    for name in names:
        agent = name_to_agent.get(name)
        if agent is not None:
            agents.append(agent)
    return agents


# ─────────────────────────────────────────────────────────────────────────────
# 全局查询方法（不限制 team）
# ─────────────────────────────────────────────────────────────────────────────


def get_agent_by_id_sync(agent_id: int) -> GtAgent | None:
    """按 ID 查询单个 agent（同步方法，优先从缓存获取）。

    若缓存未命中则从数据库同步查询，并自动填充缓存。
    """
    cached: GtAgent | None = _agent_cache.get(agent_id)
    if cached is not None:
        return cached
    version: int = _agent_cache.version()
    agent: GtAgent | None = GtAgent.get_or_none(GtAgent.id == agent_id)
    if agent is not None:
        _agent_cache.add_if_version(agent, version)
    return agent


def get_agent_name(agent_id: int) -> str:
    """获取 agent 名称（同步方法，仅用于日志输出）。

    仅从缓存查找；若不存在返回 "unknown({agent_id})"，不执行数据库查询。
    """
    agent: GtAgent | None = _agent_cache.get(agent_id)
    return agent.name if agent else f"unknown({agent_id})"


def get_agents_by_ids_sync(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents（同步方法，优先从缓存获取）。

    缓存未命中的 ID 会从数据库批量查询，并自动填充缓存。
    """
    if not agent_ids:
        return []
    agents: list[GtAgent] = []
    uncached_ids: list[int] = []
    for agent_id in agent_ids:
        cached: GtAgent | None = _agent_cache.get(agent_id)
        if cached is not None:
            agents.append(cached)
        else:
            uncached_ids.append(agent_id)
    if uncached_ids:
        version: int = _agent_cache.version()
        fetched: list[GtAgent] = list(
            GtAgent.select()
            .where(GtAgent.id.in_(uncached_ids))  # type: ignore[attr-defined]
            .execute()
        )
        _agent_cache.add_many_if_version(fetched, version)
        fetched_map: dict[int, GtAgent] = {agent.id: agent for agent in fetched}
        for agent_id in agent_ids:
            if agent_id not in [a.id for a in agents if a.id is not None]:
                fetched_agent: GtAgent | None = fetched_map.get(agent_id)
                if fetched_agent is not None:
                    agents.append(fetched_agent)
    # 保持原始顺序
    agent_map: dict[int, GtAgent] = {a.id: a for a in agents if a.id is not None}
    return [agent_map[aid] for aid in agent_ids if aid in agent_map]


async def get_agent_by_id(agent_id: int) -> GtAgent | None:
    """按 ID 查询单个 agent，不限制 team_id（包含跨团队 Agent）。

    查询结果会自动填充缓存。
    """
    cached: GtAgent | None = _agent_cache.get(agent_id)
    if cached is not None:
        return cached
    version: int = _agent_cache.version()
    agent: GtAgent | None = await GtAgent.aio_get_or_none(GtAgent.id == agent_id)
    if agent is not None:
        _agent_cache.add_if_version(agent, version)
    return agent


async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents，不限制 team_id。

    查询结果会自动填充缓存。
    """
    if not agent_ids:
        return []
    agents: list[GtAgent] = []
    uncached_ids: list[int] = []
    for agent_id in agent_ids:
        cached: GtAgent | None = _agent_cache.get(agent_id)
        if cached is not None:
            agents.append(cached)
        else:
            uncached_ids.append(agent_id)
    if uncached_ids:
        version: int = _agent_cache.version()
        fetched: list[GtAgent] = list(
            await GtAgent.select()
            .where(GtAgent.id.in_(uncached_ids))  # type: ignore[attr-defined]
            .aio_execute()
        )
        _agent_cache.add_many_if_version(fetched, version)
        agents.extend(fetched)
    # 保持原始顺序
    agent_map: dict[int, GtAgent] = {a.id: a for a in agents if a.id is not None}
    return [agent_map[aid] for aid in agent_ids if aid in agent_map]


# ─────────────────────────────────────────────────────────────────────────────
# 写入方法
# ─────────────────────────────────────────────────────────────────────────────


async def batch_save_agents(team_id: int, agents: list[GtAgent]) -> None:
    """批量保存成员：有 id 则更新，无 id 则插入。"""
    if len(agents) == 0:
        return

    invalid_team_ids: list[int] = sorted({agent.team_id for agent in agents if agent.team_id != team_id})
    if invalid_team_ids:
        raise ValueError(
            f"all agents must have team_id={team_id}, got mismatched team_ids={invalid_team_ids}"
        )

    max_num: int = await get_max_employee_number(team_id)
    next_num: int = max_num + 1

    to_create: list[GtAgent] = []
    to_update: list[GtAgent] = []

    for agent in agents:
        if agent.id is not None:
            to_update.append(agent)
        else:
            agent.employee_number = next_num
            to_create.append(agent)
            next_num += 1

    if len(to_create) > 0:
        await GtAgent.insert_many([
            {
                "team_id": agent.team_id,
                "name": agent.name,
                "role_template_id": agent.role_template_id,
                "employ_status": agent.employ_status,
                "model": agent.model,
                "driver": agent.driver,
                "employee_number": agent.employee_number,
                "i18n": agent.i18n or {},
                "allow_tools": agent.allow_tools,
                "allow_skills": agent.allow_skills,
            }
            for agent in to_create
        ]).aio_execute()

    try:
        for agent in to_update:
            await agent.aio_save()
    finally:
        for agent in to_update:
            if agent.id is not None:
                _agent_cache.invalidate(agent.id)


async def save_agent(agent: GtAgent) -> None:
    """单条更新入口：持久化 + 缓存失效边界（文档 §5.1 示意接口）。

    只处理**已存在**的 agent（id 非空）；新建仍走 `batch_save_agents`，
    因为工号分配需要 `get_max_employee_number`。纯更新路径不该为一次 MAX 查询付钱。
    """
    if agent.id is None:
        raise ValueError("save_agent 只用于更新已存在的 agent；新建请用 batch_save_agents")
    try:
        await agent.aio_save()
    finally:
        _agent_cache.invalidate(agent.id)


async def batch_update_agent_status(agent_ids: list[int], status: EmployStatus) -> None:
    """批量更新成员状态。"""
    if len(agent_ids) == 0:
        return
    await GtAgent.update(employ_status=status).where(GtAgent.id.in_(agent_ids)).aio_execute()  # type: ignore[attr-defined]
    for aid in agent_ids:
        _agent_cache.invalidate(aid)


# ─────────────────────────────────────────────────────────────────────────────
# 辅助方法
# ─────────────────────────────────────────────────────────────────────────────


async def get_max_employee_number(team_id: int) -> int:
    """获取 team 内当前最大工号。"""
    result = list(
        await GtAgent.select(fn.MAX(GtAgent.employee_number))
        .where(GtAgent.team_id == team_id)
        .aio_execute()
    )
    if not result:
        return 0
    return result[0].employee_number or 0