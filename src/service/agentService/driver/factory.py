from __future__ import annotations

from typing import Any, Mapping

from constants import DriverType
from .base import AgentDriverConfig
from .claudeSdkDriver import ClaudeSdkAgentDriver
from .nativeDriver import NativeAgentDriver
from .tspDriver import TspAgentDriver


from util.configTypes import RoleTemplate

def normalize_driver_config(role_template_cfg: RoleTemplate | Mapping[str, Any]) -> AgentDriverConfig:
    if hasattr(role_template_cfg, "model_dump"):
        role_template_cfg = role_template_cfg.model_dump()

    driver_cfg = role_template_cfg.get("driver")
    if driver_cfg:
        if isinstance(driver_cfg, DriverType):
            return AgentDriverConfig(driver_type=driver_cfg)
        # driver_cfg is a string
        driver_type = DriverType.value_of(driver_cfg) or DriverType.NATIVE
        return AgentDriverConfig(driver_type=driver_type)

    if role_template_cfg.get("use_agent_sdk", False):
        return AgentDriverConfig(
            driver_type=DriverType.CLAUDE_SDK,
            options={"allowed_tools": role_template_cfg.get("allowed_tools", [])},
        )

    return AgentDriverConfig(driver_type=DriverType.NATIVE)


def build_agent_driver(host, driver_config: AgentDriverConfig):
    driver_type = driver_config.driver_type

    if driver_type == DriverType.NATIVE:
        return NativeAgentDriver(host, driver_config)
    if driver_type == DriverType.CLAUDE_SDK:
        return ClaudeSdkAgentDriver(host, driver_config)
    if driver_type == DriverType.TSP:
        return TspAgentDriver(host, driver_config)
    raise ValueError(f"未知 agent driver 类型: {driver_type}")
