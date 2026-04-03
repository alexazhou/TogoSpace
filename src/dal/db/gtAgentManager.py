from __future__ import annotations

from peewee import fn
from typing import TypeVar

from constants import EmployStatus, DriverType, SpecialAgent
from model.dbModel.gtAgent import GtAgent

from . import gtRoleTemplateManager


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


async def get_team_agents(team_id: int) -> list[GtAgent]:
    return list(
        await GtAgent.select()
        .where(GtAgent.team_id == team_id)
        .order_by(GtAgent.name)
        .aio_execute()
    )


async def get_agent(team_id: int, name: str) -> GtAgent | None:
    return await GtAgent.aio_get_or_none(
        GtAgent.team_id == team_id,
        GtAgent.name == name,
    )


async def get_agents_by_employ_status(team_id: int, status: EmployStatus) -> list[GtAgent]:
    """按 team + employ_status 查询成员。"""
    return list(
        await GtAgent.select()
        .where(
            GtAgent.team_id == team_id,
            GtAgent.employ_status == status,
        )
        .order_by(GtAgent.name)
        .aio_execute()
    )


async def resolve_role_template_id_by_name(template_name: str) -> int:
    """按名称查找角色模板 ID。"""
    if not template_name:
        return 0

    template = await gtRoleTemplateManager.get_role_template_by_name(template_name)
    if template is None:
        return 0
    return template.id


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
            }
            for agent in to_create
        ]).aio_execute()

    for agent in to_update:
        # 更新已有记录
        await agent.aio_save()


async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents。"""
    if not agent_ids:
        return []
    return list(
        await GtAgent.select()
        .where(GtAgent.id.in_(agent_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )


def _build_special_agent(special_agent: SpecialAgent) -> GtAgent:
    return GtAgent(
        id=int(special_agent.value),
        team_id=0,
        name=special_agent.name,
        role_template_id=0,
    )


KeyT = TypeVar("KeyT", int, str)


def _build_team_agents_in_order(
    keys: list[KeyT],
    agent_map: dict[KeyT, GtAgent],
    include_special: bool,
) -> list[GtAgent]:
    agents: list[GtAgent] = []
    for key in keys:
        special_agent = SpecialAgent.value_of(key)
        if special_agent is not None:
            if include_special:
                agents.append(_build_special_agent(special_agent))
            continue

        agent = agent_map.get(key)
        if agent is not None:
            agents.append(agent)

    return agents


async def get_team_agents_by_ids(team_id: int, agent_ids: list[int], include_special: bool = False) -> list[GtAgent]:
    if not agent_ids:
        return []

    normal_agent_ids = [agent_id for agent_id in agent_ids if SpecialAgent.value_of(agent_id) is None]
    gt_agents = await get_agents_by_ids(normal_agent_ids)
    agent_map = {agent.id: agent for agent in gt_agents if agent.team_id == team_id}
    return _build_team_agents_in_order(agent_ids, agent_map, include_special)


async def get_team_agents_by_names(team_id: int, names: list[str], include_special: bool = False) -> list[GtAgent]:
    if not names:
        return []

    normal_names = [name for name in names if SpecialAgent.value_of(name) is None]
    gt_agents = []
    if normal_names:
        gt_agents = list(
            await GtAgent.select()
            .where(
                GtAgent.team_id == team_id,
                GtAgent.name.in_(normal_names),  # type: ignore[attr-defined]
            )
            .order_by(GtAgent.name)
            .aio_execute()
        )
    agent_map = {agent.name: agent for agent in gt_agents}
    return _build_team_agents_in_order(names, agent_map, include_special)


async def batch_update_agent_status(agent_ids: list[int], status: EmployStatus) -> None:
    """批量更新成员状态。"""
    if len(agent_ids) == 0:
        return
    await GtAgent.update(employ_status=status).where(GtAgent.id.in_(agent_ids)).aio_execute()  # type: ignore[attr-defined]
