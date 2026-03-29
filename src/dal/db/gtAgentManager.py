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


async def assign_employee_numbers_for_existing_agents(team_id: int) -> int:
    """为 employee_number=0 的 agents 分配工号。返回已分配的数量。"""
    agents = list(
        await GtAgent.select()
        .where((GtAgent.team_id == team_id) & (GtAgent.employee_number == 0))
        .order_by(GtAgent.name)
        .aio_execute()
    )

    if not agents:
        return 0

    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1
    assigned_count = 0

    for agent in agents:
        existing = await GtAgent.aio_get_or_none(
            (GtAgent.team_id == team_id) &
            (GtAgent.employee_number == next_num)
        )
        if existing is None:
            agent.employee_number = next_num
            await agent.aio_save()
            next_num += 1
            assigned_count += 1

    return assigned_count


async def save_members_full_replace(team_id: int, members: list[Any]) -> list[GtAgent]:
    """全量覆盖成员列表：有 id 更新，无 id 创建，不在列表的设为离职状态。返回在职成员列表。"""
    existing_agents = await get_agents_by_team(team_id)
    existing_ids = {a.id for a in existing_agents}
    existing_by_id = {a.id: a for a in existing_agents}

    request_ids = {m.id for m in members if m.id is not None}

    ids_to_offboard = existing_ids - request_ids
    if ids_to_offboard:
        for agent_id in ids_to_offboard:
            agent = existing_by_id[agent_id]
            agent.employ_status = EmployStatus.OFF_BOARD
            await agent.aio_save()

    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1

    for member in members:
        role_template_id = await _resolve_role_template_id(member)
        if member.id is not None and member.id in existing_by_id:
            agent = existing_by_id[member.id]
            agent.name = member.name
            agent.role_template_id = role_template_id
            agent.model = member.model
            agent.driver = member.driver
            agent.employ_status = EmployStatus.ON_BOARD
            await agent.aio_save()

    for member in members:
        if member.id is None:
            role_template_id = await _resolve_role_template_id(member)
            try:
                await GtAgent.insert(
                    team_id=team_id,
                    name=member.name,
                    role_template_id=role_template_id,
                    model=member.model,
                    driver=member.driver,
                    employee_number=next_num,
                    employ_status=EmployStatus.ON_BOARD,
                ).aio_execute()
                next_num += 1
            except IntegrityError as e:
                raise TeamAgentException(
                    error_message=f'成员名称"{member.name}"已存在',
                    error_code="MEMBER_NAME_EXISTS",
                ) from e

    return await get_on_board_agents(team_id)
