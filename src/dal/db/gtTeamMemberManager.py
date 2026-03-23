from __future__ import annotations

from model.dbModel.gtTeamMember import GtTeamMember
from util.configTypes import TeamMemberConfig


async def get_members_by_team(team_id: int) -> list[GtTeamMember]:
    return list(
        await GtTeamMember.select()
        .where(GtTeamMember.team_id == team_id)
        .order_by(GtTeamMember.name)
        .aio_execute()
    )


async def get_member(team_id: int, name: str) -> GtTeamMember | None:
    return await GtTeamMember.aio_get_or_none(
        (GtTeamMember.team_id == team_id) &
        (GtTeamMember.name == name)
    )


async def upsert_team_members(team_id: int, members: list[TeamMemberConfig]) -> None:
    await delete_members_by_team(team_id)
    if not members:
        return

    rows = [
        {
            "team_id": team_id,
            "name": member["name"],
            "agent_name": member["agent"],
        }
        for member in members
    ]
    await GtTeamMember.insert_many(rows).aio_execute()


async def delete_members_by_team(team_id: int) -> None:
    await GtTeamMember.delete().where(GtTeamMember.team_id == team_id).aio_execute()
