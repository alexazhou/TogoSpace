from __future__ import annotations

from model.dbModel.gtRoleTemplate import GtRoleTemplate


async def get_role_template_by_name(template_name: str) -> GtRoleTemplate | None:
    """通过名称获取单个 role template。"""
    return await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.name == template_name)


async def get_role_template_by_id(template_id: int) -> GtRoleTemplate | None:
    """通过 ID 获取单个 role template。"""
    return await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.id == template_id)


async def get_role_templates_by_ids(template_ids: list[int]) -> list[GtRoleTemplate]:
    """按 ID 批量获取 role templates。"""
    if not template_ids:
        return []
    return list(
        await GtRoleTemplate.select()
        .where(GtRoleTemplate.id.in_(template_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )


async def get_all_role_templates() -> list[GtRoleTemplate]:
    """获取所有 role templates。"""
    query = GtRoleTemplate.select().order_by(GtRoleTemplate.name)
    return list(await query.aio_execute())


async def save_role_template(template: GtRoleTemplate) -> GtRoleTemplate:
    """按对象保存 role template。

    - 有 id：按主键更新
    - 无 id：按 name 执行 upsert
    """
    if template.id is not None:
        await template.aio_save()
        updated = await get_role_template_by_id(template.id)
        if updated is None:
            raise RuntimeError(f"role template update failed: {template.id}")
        return updated

    await (
        GtRoleTemplate.insert(
            name=template.name,
            model=template.model,
            soul=template.soul,
            type=template.type,
            allowed_tools=template.allowed_tools,
            i18n=template.i18n or {},
        )
        .on_conflict(
            conflict_target=[GtRoleTemplate.name],
            update={
                GtRoleTemplate.model: template.model,
                GtRoleTemplate.soul: template.soul,
                GtRoleTemplate.type: template.type,
                GtRoleTemplate.allowed_tools: template.allowed_tools,
                GtRoleTemplate.i18n: template.i18n or {},
            },
        )
        .aio_execute()
    )
    created = await get_role_template_by_name(template.name)
    if created is None:
        raise RuntimeError(f"role template save failed: {template.name}")
    return created


async def delete_role_template(template_id: int) -> bool:
    """删除指定 role template。"""
    deleted = await (
        GtRoleTemplate.delete()
        .where(GtRoleTemplate.id == template_id)
        .aio_execute()
    )
    return bool(deleted)
