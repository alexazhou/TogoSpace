from __future__ import annotations

from peewee import fn

from constants import EmployStatus, DriverType
from model.dbModel.gtAgent import GtAgent


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


async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents，不限制 team_id。"""
    if not agent_ids:
        return []
    return list(
        await GtAgent.select()
        .where(GtAgent.id.in_(agent_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )


# ─────────────────────────────────────────────────────────────────────────────
# 写入方法
# ─────────────────────────────────────────────────────────────────────────────


async def batch_save_agents(team_id: int, agents: list[GtAgent]) -> None:
    """批量保存成员：有 id 则更新，无 id 则插入。"""
    if len(agents) == 0:
        return

    invalid_team_ids = sorted({agent.team_id for agent in agents if agent.team_id != team_id})
    if invalid_team_ids:
        raise ValueError(
            f"all agents must have team_id={team_id}, got mismatched team_ids={invalid_team_ids}"
        )

    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1

    to_create = []
    to_update = []

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
            }
            for agent in to_create
        ]).aio_execute()

    for agent in to_update:
        await agent.aio_save()


async def batch_update_agent_status(agent_ids: list[int], status: EmployStatus) -> None:
    """批量更新成员状态。"""
    if len(agent_ids) == 0:
        return
    await GtAgent.update(employ_status=status).where(GtAgent.id.in_(agent_ids)).aio_execute()  # type: ignore[attr-defined]


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