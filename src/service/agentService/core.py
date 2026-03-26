import logging
from typing import List

from util import configUtil
from util.configTypes import AgentTemplate

logger = logging.getLogger(__name__)

_agent_templates: dict[str, AgentTemplate] = {}


async def startup() -> None:
    global _agent_templates
    _agent_templates = {}


def load_agent_config(agents_config: List[AgentTemplate] | None = None) -> None:
    """加载 Agent 模版配置。"""
    global _agent_templates
    resolved = agents_config if agents_config is not None else configUtil.get_app_config().agents
    _agent_templates = {cfg.name: cfg for cfg in resolved}
    logger.info(f"加载 Agent 模版: {list(_agent_templates.keys())}")


def get_all_agent_definitions() -> list[AgentTemplate]:
    return list(_agent_templates.values())


def get_agent_definition(template_name: str) -> AgentTemplate | None:
    return _agent_templates.get(template_name)


async def shutdown() -> None:
    global _agent_templates
    _agent_templates = {}
