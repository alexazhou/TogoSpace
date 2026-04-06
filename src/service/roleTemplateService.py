import logging

from dal.db import gtRoleTemplateManager
from model.dbModel.gtRoleTemplate import GtRoleTemplate

logger = logging.getLogger(__name__)


async def startup() -> None:
    return None


async def save_role_template(role_template: GtRoleTemplate) -> GtRoleTemplate:
    """保存 role template。已存在时同步字段，不存在时创建。"""
    existing = await gtRoleTemplateManager.get_role_template_by_name(role_template.name)
    if existing is not None:
        if (
            existing.type != role_template.type
            or existing.allowed_tools != role_template.allowed_tools
        ):
            return await gtRoleTemplateManager.save_role_template(
                GtRoleTemplate(
                    name=role_template.name,
                    model=existing.model,
                    soul=existing.soul,
                    type=role_template.type,
                    allowed_tools=role_template.allowed_tools,
                )
            )
        return existing

    created = await gtRoleTemplateManager.save_role_template(role_template)
    logger.info("Role template '%s' 已保存", role_template.name)
    return created


async def shutdown() -> None:
    return None
