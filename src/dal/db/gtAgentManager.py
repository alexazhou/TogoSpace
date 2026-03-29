from __future__ import annotations

from typing import Any

from peewee import fn, IntegrityError

from constants import EmployStatus, DriverType
from exception import TeamAgentException
from model.dbModel.gtAgent import GtAgent
from util.configTypes import AgentConfig

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


async def _resolve_role_template_id(member: AgentConfig | dict[str, Any] | Any) -> int:
    if isinstance(member, dict):
        raw_id = member.get("role_template_id")
        template_name = member.get("role_template")
    else:
        raw_id = getattr(member, "role_template_id", None)
        template_name = getattr(member, "role_template", None)

    if isinstance(raw_id, int):
        return raw_id

    if not template_name:
        raise TeamAgentException(
            error_message="成员缺少 role_template_id",
            error_code="ROLE_TEMPLATE_ID_REQUIRED",
        )

    template = await gtRoleTemplateManager.get_role_template_by_name(str(template_name))
    if template is None:
        raise TeamAgentException(
            error_message=f"角色模板不存在: {template_name}",
            error_code="ROLE_TEMPLATE_NOT_FOUND",
        )
    return template.id


def _normalize_driver_value(driver_raw: Any) -> DriverType:
    if isinstance(driver_raw, str):
        return DriverType.value_of(driver_raw) or DriverType.NATIVE
    if isinstance(driver_raw, DriverType):
        return driver_raw
    return DriverType.NATIVE


async def upsert_agents(team_id: int, members: list[AgentConfig] | list[dict[str, Any]]) -> None:
    """增量更新 team 的成员列表。有 id 则按 id 更新，无 id 则插入。"""
    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1

    for member in members:
        if isinstance(member, dict):
            member_id = member.get("id")
            name = member.get("name")
            model = member.get("model") or ""
            driver = _normalize_driver_value(member.get("driver"))
        else:
            member_id = None
            name = member.name
            model = member.model or ""
            driver = _normalize_driver_value(member.driver)

        role_template_id = await _resolve_role_template_id(member)

        if member_id:
            existing = await GtAgent.aio_get_or_none(GtAgent.id == member_id)
            if existing is not None:
                existing.name = name
                existing.role_template_id = role_template_id
                existing.model = model
                existing.driver = driver
                await existing.aio_save()
        else:
            await GtAgent.insert(
                team_id=team_id,
                name=name,
                role_template_id=role_template_id,
                model=model,
                driver=driver,
                employee_number=next_num,
            ).aio_execute()
            next_num += 1


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
    if not agent_ids:
        return
    await GtAgent.update(employ_status=status).where(GtAgent.id.in_(agent_ids)).aio_execute()  # type: ignore[attr-defined]


async def batch_save_agents(agents_data: list[dict[str, Any]]) -> None:
    """批量保存成员：有 id 则更新，无 id 则插入。"""
    if len(agents_data) == 0:
        return

    to_create = []
    to_update = []
    for data in agents_data:
        if data.get("id") is not None:
            to_update.append(data)
        else:
            to_create.append(data)

    if len(to_create) > 0:
        await GtAgent.insert_many(to_create).aio_execute()

    for data in to_update:
        update_data = data.copy()
        agent_id = update_data.pop("id")
        await GtAgent.update(**update_data).where(GtAgent.id == agent_id).aio_execute()
