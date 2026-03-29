from __future__ import annotations

from typing import Any

from peewee import fn, IntegrityError

from constants import EmployStatus, DriverType
from exception import TeamAgentException
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


async def get_agents_by_team(team_id: int) -> list[GtAgent]:
    return list(
        await GtAgent.select()
        .where(GtAgent.team_id == team_id)
        .order_by(GtAgent.name)
        .aio_execute()
    )


async def get_agents_by_role_template_id(role_template_id: int) -> list[GtAgent]:
    return list(
        await GtAgent.select()
        .where(GtAgent.role_template_id == role_template_id)
        .order_by(GtAgent.name)
        .aio_execute()
    )


async def get_agent(team_id: int, name: str) -> GtAgent | None:
    return await GtAgent.aio_get_or_none(
        (GtAgent.team_id == team_id) &
        (GtAgent.name == name)
    )


async def get_on_board_agents(team_id: int) -> list[GtAgent]:
    return list(
        await GtAgent.select()
        .where((GtAgent.team_id == team_id) & (GtAgent.employ_status == EmployStatus.ON_BOARD))
        .order_by(GtAgent.name)
        .aio_execute()
    )


async def get_off_board_agents(team_id: int) -> list[GtAgent]:
    return list(
        await GtAgent.select()
        .where((GtAgent.team_id == team_id) & (GtAgent.employ_status == EmployStatus.OFF_BOARD))
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

    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1

    to_create = []
    to_update = []

    for agent in agents:
        if agent.id is not None:
            to_update.append(agent)
        else:
            agent.team_id = team_id
            agent.employee_number = next_num
            to_create.append(agent)
            next_num += 1

    if len(to_create) > 0:
        # 批量插入新记录
        await GtAgent.bulk_create(to_create)

    for agent in to_update:
        # 更新已有记录
        await agent.aio_save()


async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents。"""
    return list(
        await GtAgent.select()
        .where(GtAgent.id.in_(agent_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )


async def update_agent(agent_id: int, name: str, role_template_id: int, model: str, driver: DriverType) -> GtAgent:
    """按 ID 更新单个 agent。"""
    agent = await GtAgent.aio_get_or_none(GtAgent.id == agent_id)
    if agent is None:
        raise ValueError(f"Agent ID '{agent_id}' not found")

    agent.name = name
    agent.role_template_id = role_template_id
    agent.model = model
    agent.driver = driver
    await agent.aio_save()

    return agent


async def batch_update_agent_status(agent_ids: list[int], status: EmployStatus) -> None:
    """批量更新成员状态。"""
    if len(agent_ids) == 0:
        return
    await GtAgent.update(employ_status=status).where(GtAgent.id.in_(agent_ids)).aio_execute()  # type: ignore[attr-defined]
