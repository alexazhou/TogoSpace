import logging

from constants import RoleTemplateType
from dal.db import gtRoleTemplateManager
from model.dbModel.gtRoleTemplate import GtRoleTemplate

logger = logging.getLogger(__name__)


async def startup() -> None:
    return None


async def import_role_template(
    name: str,
    soul: str = "",
    model: str | None = None,
    driver=None,
    allowed_tools: list[str] | None = None,
) -> None:
    """导入 role template 到数据库。已存在时同步 driver / allowed_tools。"""
    existing = await gtRoleTemplateManager.get_role_template_by_name(name)
    if existing is not None:
        if (
            existing.type != RoleTemplateType.SYSTEM
            or existing.driver != driver
            or existing.allowed_tools != allowed_tools
        ):
            await gtRoleTemplateManager.save_role_template(
                GtRoleTemplate(
                    name=name,
                    model=existing.model,
                    soul=existing.soul,
                    type=RoleTemplateType.SYSTEM,
                    driver=driver,
                    allowed_tools=allowed_tools,
                )
            )
        return

    await gtRoleTemplateManager.save_role_template(
        GtRoleTemplate(
            name=name,
            model=model,
            soul=soul,
            type=RoleTemplateType.SYSTEM,
            driver=driver,
            allowed_tools=allowed_tools,
        )
    )
    logger.info(f"Role template '{name}' 已导入数据库")


async def shutdown() -> None:
    return None
