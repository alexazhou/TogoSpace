from __future__ import annotations

from constants import DriverType, RoleTemplateType
from model.dbModel.gtRoleTemplate import GtRoleTemplate


async def upsert_role_template(
    template_name: str,
    model: str | None,
    soul: str = "",
    template_type: RoleTemplateType = RoleTemplateType.SYSTEM,
    driver: DriverType | None = None,
    allowed_tools: list[str] | None = None,
) -> GtRoleTemplate:
    """创建或更新 role template。"""
    await (
        GtRoleTemplate.insert(
            template_name=template_name,
            model=model,
            soul=soul,
            type=template_type,
            driver=driver,
            allowed_tools=allowed_tools,
        )
        .on_conflict(
            conflict_target=[GtRoleTemplate.template_name],
            update={
                GtRoleTemplate.model: model,
                GtRoleTemplate.soul: soul,
                GtRoleTemplate.type: template_type,
                GtRoleTemplate.driver: driver,
                GtRoleTemplate.allowed_tools: allowed_tools,
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
    """获取单个 role template。"""
    return await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.template_name == template_name)


async def get_all_role_templates() -> list[GtRoleTemplate]:
    """获取所有 role templates。"""
    query = GtRoleTemplate.select().order_by(GtRoleTemplate.template_name)
    return list(await query.aio_execute())


async def update_role_template(
    template_name: str,
    soul: str | None = None,
    model: str | None = None,
    driver: DriverType | None = None,
    allowed_tools: list[str] | None = None,
) -> GtRoleTemplate:
    """更新 role template 的指定字段。"""
    row = await get_role_template(template_name)
    if row is None:
        raise RuntimeError(f"role template not found: {template_name}")

    update_fields = {GtRoleTemplate.updated_at: GtRoleTemplate._now()}
    if soul is not None:
        update_fields[GtRoleTemplate.soul] = soul
    if model is not None:
        update_fields[GtRoleTemplate.model] = model
    if driver is not None:
        update_fields[GtRoleTemplate.driver] = driver
    if allowed_tools is not None:
        update_fields[GtRoleTemplate.allowed_tools] = allowed_tools

    await (
        GtRoleTemplate.update(**update_fields)
        .where(GtRoleTemplate.template_name == template_name)
        .aio_execute()
    )

    return await get_role_template(template_name)
