from __future__ import annotations

from peewee import fn
from constants import EmployStatus, DriverType
from model.dbModel.gtAgent import GtAgent
from util.configTypes import AgentConfig


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


async def upsert_agents(team_id: int, members: list[AgentConfig] | list[dict]) -> None:
    """增量更新 team 的成员列表。有 id 则按 id 更新，无 id 则插入。"""
    # 获取当前最大工号，新 agents 从 next_num 开始分配
    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1

    for member in members:
        if isinstance(member, dict):
            member_id = member.get("id")
            name = member.get("name")
            role_template = member.get("role_template") or member.get("role_template_name")
            model = member.get("model") or ""
            driver_raw = member.get("driver")
            if isinstance(driver_raw, str):
                driver = DriverType.value_of(driver_raw) or DriverType.NATIVE
            elif isinstance(driver_raw, DriverType):
                driver = driver_raw
            else:
                driver = DriverType.NATIVE
        else:
            member_id = None
            name = member.name
            role_template = member.role_template
            model = member.model or ""
            driver = member.driver if isinstance(member.driver, DriverType) else DriverType.NATIVE

        if member_id:
            # 按 id 更新：不改变工号
            existing = await GtAgent.aio_get_or_none(GtAgent.id == member_id)
            if existing is not None:
                existing.name = name
                existing.role_template_name = role_template
                existing.model = model
                existing.driver = driver
                await existing.aio_save()
        else:
            # 无 id 则插入，自动分配工号
            await GtAgent.insert(
                team_id=team_id,
                name=name,
                role_template_name=role_template,
                model=model,
                driver=driver,
                employee_number=next_num,
            ).aio_execute()
            next_num += 1


async def delete_agents_by_team(team_id: int) -> None:
    await GtAgent.delete().where(GtAgent.team_id == team_id).aio_execute()


async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents。"""
    return list(
        await GtAgent.select()
        .where(GtAgent.id.in_(agent_ids))
        .aio_execute()
    )


async def update_agent(agent_id: int, name: str, role_template_name: str, model: str, driver: DriverType) -> GtAgent:
    """按 ID 更新单个 agent。"""
    agent = await GtAgent.aio_get_or_none(GtAgent.id == agent_id)
    if agent is None:
        raise ValueError(f"Agent ID '{agent_id}' not found")

    agent.name = name
    agent.role_template_name = role_template_name
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

    # 获取当前最大工号
    max_num = await get_max_employee_number(team_id)
    next_num = max_num + 1
    assigned_count = 0

    for agent in agents:
        # 检查工号是否已被占用（跳过已占用的）
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
