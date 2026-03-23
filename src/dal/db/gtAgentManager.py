from __future__ import annotations

from model.dbModel.gtAgent import GtAgent


async def upsert_agent(team_id: int, name: str, model: str, template_name: str = "") -> GtAgent:
    await (
        GtAgent.insert(
            team_id=team_id,
            name=name,
            model=model,
            template_name=template_name,
        )
        .on_conflict(
            conflict_target=[GtAgent.team_id, GtAgent.name],
            update={
                GtAgent.model: model,
                GtAgent.template_name: template_name,
                GtAgent.updated_at: GtAgent._now_iso(),
            },
        )
        .aio_execute()
    )

    row = await GtAgent.aio_get_or_none(
        (GtAgent.team_id == team_id) &
        (GtAgent.name == name)
    )
    if row is None:
        raise RuntimeError(f"agent upsert failed: {name}@{team_id}")
    return row


async def get_agent(team_id: int, name: str) -> GtAgent | None:
    return await GtAgent.aio_get_or_none(
        (GtAgent.team_id == team_id) &
        (GtAgent.name == name)
    )


async def get_agents_by_team(team_id: int) -> list[GtAgent]:
    return await (
        GtAgent
        .select()
        .where(GtAgent.team_id == team_id)
        .order_by(GtAgent.name)
        .aio_execute()
    )
