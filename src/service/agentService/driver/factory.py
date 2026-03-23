from __future__ import annotations

from typing import Any, Mapping

from .base import AgentDriverConfig
from .claudeSdkDriver import ClaudeSdkAgentDriver
from .nativeDriver import NativeAgentDriver


def normalize_driver_config(agent_cfg: Mapping[str, Any]) -> AgentDriverConfig:
    driver_cfg = agent_cfg.get("driver")
    if driver_cfg:
        return AgentDriverConfig(
            driver_type=driver_cfg["type"],
            options={k: v for k, v in driver_cfg.items() if k != "type"},
        )

    runtime_cfg = agent_cfg.get("runtime")
    if runtime_cfg:
        return AgentDriverConfig(
            driver_type=runtime_cfg["type"],
            options={k: v for k, v in runtime_cfg.items() if k != "type"},
        )

    if agent_cfg.get("use_agent_sdk", False):
        return AgentDriverConfig(
            driver_type="claude_sdk",
            options={"allowed_tools": agent_cfg.get("allowed_tools", [])},
        )

    return AgentDriverConfig(driver_type="native")


def build_agent_driver(host, driver_config: AgentDriverConfig):
    if driver_config.driver_type == "native":
        return NativeAgentDriver(host, driver_config)
    if driver_config.driver_type == "claude_sdk":
        return ClaudeSdkAgentDriver(host, driver_config)
    raise ValueError(f"未知 agent driver 类型: {driver_config.driver_type}")
