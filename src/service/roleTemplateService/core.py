import logging

from constants import RoleTemplateType
from dal.db import gtRoleTemplateManager
from util import configUtil
from util.configTypes import RoleTemplateConfig

logger = logging.getLogger(__name__)


async def startup() -> None:
    configs = configUtil.get_app_config().role_templates

    for template in configs:
        await _import_role_template_from_config(template)

    db_templates = await gtRoleTemplateManager.get_all_role_templates()
    logger.info(f"加载角色模版: {[t.template_name for t in db_templates]}")


async def _import_role_template_from_config(config: RoleTemplateConfig) -> None:
    """导入 role template 到数据库。已存在时同步 driver / allowed_tools。"""
    existing = await gtRoleTemplateManager.get_role_template(config.name)
    if existing is not None:
        if (
            existing.type != RoleTemplateType.SYSTEM
            or existing.driver != config.driver
            or existing.allowed_tools != config.allowed_tools
        ):
            await gtRoleTemplateManager.upsert_role_template(
                config.name,
                existing.model,
                existing.soul,
                RoleTemplateType.SYSTEM,
                driver=config.driver,
                allowed_tools=config.allowed_tools,
            )
        return

    await gtRoleTemplateManager.upsert_role_template(
        config.name,
        config.model,
        config.soul,
        RoleTemplateType.SYSTEM,
        config.driver,
        config.allowed_tools,
    )
    logger.info(f"Role template '{config.name}' 已导入数据库")


async def shutdown() -> None:
    return None
