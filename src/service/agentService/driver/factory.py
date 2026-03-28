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
        driver_type = driver_cfg if isinstance(driver_cfg, DriverType) else driver_cfg.get("type") if isinstance(driver_cfg, dict) else driver_cfg
        if isinstance(driver_type, DriverType):
            return AgentDriverConfig(driver_type=driver_type.value)
        return AgentDriverConfig(
            driver_type=driver_type,
            options={k: v for k, v in driver_cfg.items() if k != "type"} if isinstance(driver_cfg, dict) else {},
        )

    runtime_cfg = role_template_cfg.get("runtime")
    if runtime_cfg:
        driver_type = runtime_cfg if isinstance(runtime_cfg, DriverType) else runtime_cfg.get("type") if isinstance(runtime_cfg, dict) else runtime_cfg
        if isinstance(driver_type, DriverType):
            return AgentDriverConfig(driver_type=driver_type.value)
        return AgentDriverConfig(
            driver_type=driver_type,
            options={k: v for k, v in runtime_cfg.items() if k != "type"} if isinstance(runtime_cfg, dict) else {},
        )

    if role_template_cfg.get("use_agent_sdk", False):
        return AgentDriverConfig(
            driver_type=DriverType.CLAUDE_SDK.value,
            options={"allowed_tools": role_template_cfg.get("allowed_tools", [])},
        )

    return AgentDriverConfig(driver_type=DriverType.NATIVE.value)


def build_agent_driver(host, driver_config: AgentDriverConfig):
    driver_type = driver_config.driver_type
    if isinstance(driver_type, str):
        driver_type = DriverType.value_of(driver_type) or DriverType.NATIVE

    if driver_type == DriverType.NATIVE:
        return NativeAgentDriver(host, driver_config)
    if driver_type == DriverType.CLAUDE_SDK:
        return ClaudeSdkAgentDriver(host, driver_config)
    if driver_type == DriverType.TSP:
        return TspAgentDriver(host, driver_config)
    raise ValueError(f"未知 agent driver 类型: {driver_config.driver_type}")
