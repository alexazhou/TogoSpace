from __future__ import annotations

from typing import Any, Mapping

from constants import DriverType
from .base import AgentDriverConfig
from .nativeDriver import NativeAgentDriver
from .claudeSdkDriver import ClaudeSdkAgentDriver
from .tspDriver import TspAgentDriver

from util.configTypes import RoleTemplateConfig

def normalize_driver_config(role_template_cfg: RoleTemplateConfig | Mapping[str, Any]) -> AgentDriverConfig:
    if hasattr(role_template_cfg, "model_dump"):
        role_template_cfg = role_template_cfg.model_dump()

    driver_cfg = role_template_cfg.get("driver")
    if driver_cfg:
        if isinstance(driver_cfg, Mapping):
            driver_type = DriverType.value_of(driver_cfg.get("type")) or DriverType.NATIVE
            options = {k: v for k, v in driver_cfg.items() if k != "type"}
            return AgentDriverConfig(driver_type=driver_type, options=options)
        if isinstance(driver_cfg, DriverType):
            options = {}
            if driver_cfg == DriverType.CLAUDE_SDK and role_template_cfg.get("allowed_tools") is not None:
                options["allowed_tools"] = role_template_cfg.get("allowed_tools", [])
            return AgentDriverConfig(driver_type=driver_cfg, options=options)
        driver_type = DriverType.value_of(driver_cfg) or DriverType.NATIVE
        options = {}
        if driver_type == DriverType.CLAUDE_SDK and role_template_cfg.get("allowed_tools") is not None:
            options["allowed_tools"] = role_template_cfg.get("allowed_tools", [])
        return AgentDriverConfig(driver_type=driver_type, options=options)

    runtime_cfg = role_template_cfg.get("runtime")
    if isinstance(runtime_cfg, Mapping):
        driver_type = DriverType.value_of(runtime_cfg.get("type")) or DriverType.NATIVE
        options = {k: v for k, v in runtime_cfg.items() if k != "type"}
        return AgentDriverConfig(driver_type=driver_type, options=options)

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
