import operator
from functools import reduce
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


async def search_role_templates(keywords: list[str]) -> list[GtRoleTemplate]:
    """按关键词搜索 role templates（匹配 name、soul 或 i18n）。
    多个关键词之间使用 OR 逻辑：命中任何一个词即可。
    在内存中进行过滤以保证 i18n 匹配的一致性。
    """
    templates = await get_all_role_templates()
    if not keywords:
        return templates

    keywords = [k.lower() for k in keywords]
    result = []
    
    for t in templates:
        # 待搜索的文本池
        search_texts = [t.name.lower(), t.soul.lower()]
        # 加入所有多语言数据中的文本值
        if t.i18n:
            def collect_values(d):
                for v in d.values():
                    if isinstance(v, dict):
                        collect_values(v)
                    elif isinstance(v, str):
                        search_texts.append(v.lower())
            collect_values(t.i18n)
        
        # 命中逻辑：任意一个关键词在任意一个搜索文本中出现
        matched = False
        for kw in keywords:
            if any(kw in text for text in search_texts):
                matched = True
                break
        
        if matched:
            result.append(t)
            
    return result


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


async def resolve_role_template_id_by_name(template_name: str) -> int:
    """按名称查找角色模板 ID，若不存在或名称为空则返回 0。"""
    if not template_name:
        return 0

    template = await get_role_template_by_name(template_name)
    if template is None:
        return 0
    return template.id
