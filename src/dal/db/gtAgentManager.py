from __future__ import annotations

import json

from constants import EmployStatus
from model.dbModel.gtAgent import GtAgent
from util.configTypes import AgentConfig


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
    for member in members:
        if isinstance(member, dict):
            member_id = member.get("id")
            name = member.get("name")
            role_template = member.get("role_template") or member.get("role_template_name")
            model = member.get("model") or ""
            driver = member.get("driver") or "{}"
            if isinstance(driver, dict):
                driver = json.dumps(driver, ensure_ascii=False, sort_keys=True)
        else:
            member_id = None
            name = member.name
            role_template = member.role_template
            model = member.model or ""
            driver = json.dumps(member.driver, ensure_ascii=False, sort_keys=True)

        if member_id:
            # 按 id 更新
            existing = await GtAgent.aio_get_or_none(GtAgent.id == member_id)
            if existing is not None:
                existing.name = name
                existing.role_template_name = role_template
                existing.model = model
                existing.driver = driver
                await existing.aio_save()
        else:
            # 无 id 则插入
            await GtAgent.insert(
                team_id=team_id,
                name=name,
                role_template_name=role_template,
                model=model,
                driver=driver,
            ).aio_execute()


async def delete_agents_by_team(team_id: int) -> None:
    await GtAgent.delete().where(GtAgent.team_id == team_id).aio_execute()


async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    """按 ID 列表查询 agents。"""
    return list(
        await GtAgent.select()
        .where(GtAgent.id.in_(agent_ids))
        .aio_execute()
    )


async def update_agent(agent_id: int, name: str, role_template_name: str, model: str, driver: str) -> GtAgent:
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
