from __future__ import annotations

from model.dbModel.gtRoleTemplate import GtRoleTemplate


async def upsert_role_template(template_name: str, model: str) -> GtRoleTemplate:
    await (
        GtRoleTemplate.insert(
            template_name=template_name,
            model=model,
        )
        .on_conflict(
            conflict_target=[GtRoleTemplate.template_name],
            update={
                GtRoleTemplate.model: model,
                GtRoleTemplate.updated_at: GtRoleTemplate._now(),
            },
        )
        .aio_execute()
    )

    row = await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.template_name == template_name)
    if row is None:
        raise RuntimeError(f"role template upsert failed: {template_name}")
    return row


async def get_role_template(template_name: str) -> GtRoleTemplate | None:
    return await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.template_name == template_name)
