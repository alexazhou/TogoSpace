from __future__ import annotations

from model.dbModel.gtAgent import GtAgent


async def upsert_agent(template_name: str, model: str) -> GtAgent:
    await (
        GtAgent.insert(
            template_name=template_name,
            model=model,
        )
        .on_conflict(
            conflict_target=[GtAgent.template_name],
            update={
                GtAgent.model: model,
                GtAgent.updated_at: GtAgent._now_iso(),
            },
        )
        .aio_execute()
    )

    row = await GtAgent.aio_get_or_none(GtAgent.template_name == template_name)
    if row is None:
        raise RuntimeError(f"agent upsert failed: {template_name}")
    return row


async def get_agent(template_name: str) -> GtAgent | None:
    return await GtAgent.aio_get_or_none(GtAgent.template_name == template_name)
