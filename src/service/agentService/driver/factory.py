from __future__ import annotations

from typing import Any, Mapping

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
        return AgentDriverConfig(
            driver_type=driver_cfg["type"],
            options={k: v for k, v in driver_cfg.items() if k != "type"},
        )

    runtime_cfg = role_template_cfg.get("runtime")
    if runtime_cfg:
        return AgentDriverConfig(
            driver_type=runtime_cfg["type"],
            options={k: v for k, v in runtime_cfg.items() if k != "type"},
        )

    if role_template_cfg.get("use_agent_sdk", False):
        return AgentDriverConfig(
            driver_type="claude_sdk",
            options={"allowed_tools": role_template_cfg.get("allowed_tools", [])},
        )

    return AgentDriverConfig(driver_type="native")


def build_agent_driver(host, driver_config: AgentDriverConfig):
    if driver_config.driver_type == "native":
        return NativeAgentDriver(host, driver_config)
    if driver_config.driver_type == "claude_sdk":
        return ClaudeSdkAgentDriver(host, driver_config)
    if driver_config.driver_type == "tsp":
        return TspAgentDriver(host, driver_config)
    raise ValueError(f"未知 agent driver 类型: {driver_config.driver_type}")
