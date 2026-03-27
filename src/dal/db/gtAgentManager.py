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


async def upsert_agents(team_id: int, members: list[AgentConfig]) -> None:
    await delete_agents_by_team(team_id)
    if not members:
        return

    rows = [
        {
            "team_id": team_id,
            "name": member.name,
            "role_template_name": member.role_template,
            "model": member.model or "",
            "driver": json.dumps(member.driver, ensure_ascii=False, sort_keys=True),
        }
        for member in members
    ]
    await GtAgent.insert_many(rows).aio_execute()


async def delete_agents_by_team(team_id: int) -> None:
    await GtAgent.delete().where(GtAgent.team_id == team_id).aio_execute()
