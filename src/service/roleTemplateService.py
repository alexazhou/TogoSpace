import logging

from dal.db import gtRoleTemplateManager
from model.dbModel.gtRoleTemplate import GtRoleTemplate

logger = logging.getLogger(__name__)


async def startup() -> None:
    return None


async def save_role_template(role_template: GtRoleTemplate) -> GtRoleTemplate:
    """保存 role template。存在则全字段更新，不存在则创建。"""
    saved = await gtRoleTemplateManager.save_role_template(role_template)
    logger.info("Role template '%s' 已保存", role_template.name)
    return saved


async def shutdown() -> None:
    return None
