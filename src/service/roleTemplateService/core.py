import logging
from typing import List

from util import configUtil
from util.configTypes import RoleTemplate

logger = logging.getLogger(__name__)

_role_templates: dict[str, RoleTemplate] = {}


async def startup() -> None:
    global _role_templates
    _role_templates = {}


def load_role_template_config(role_templates_config: List[RoleTemplate] | None = None) -> None:
    """加载角色模版配置。"""
    global _role_templates
    resolved = role_templates_config if role_templates_config is not None else configUtil.get_app_config().role_templates
    _role_templates = {cfg.name: cfg for cfg in resolved}
    logger.info(f"加载角色模版: {list(_role_templates.keys())}")


def get_all_role_templates() -> list[RoleTemplate]:
    return list(_role_templates.values())


def get_role_template(template_name: str) -> RoleTemplate | None:
    return _role_templates.get(template_name)


async def shutdown() -> None:
    global _role_templates
    _role_templates = {}
