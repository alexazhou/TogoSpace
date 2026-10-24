import logging

from dal.db import gtRoleTemplateManager
from model.coreModel.serviceStatus import ServiceStatusInfo
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service import serviceUtil

logger = logging.getLogger(__name__)

s_status = ServiceStatusInfo(name="roleTemplateService")


@serviceUtil.service_start_wrapper(s_status)
async def startup() -> None:
    return None


async def save_role_template(role_template: GtRoleTemplate) -> GtRoleTemplate:
    """保存 role template。存在则全字段更新，不存在则创建。"""
    saved = await gtRoleTemplateManager.save_role_template(role_template)
    logger.info("Role template '%s' 已保存", role_template.name)
    return saved


@serviceUtil.service_stop_wrapper(s_status)
async def shutdown() -> None:
    return None
