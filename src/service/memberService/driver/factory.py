from __future__ import annotations

from typing import Any, Mapping

from .base import MemberDriverConfig
from .claudeSdkDriver import ClaudeSdkMemberDriver
from .nativeDriver import NativeMemberDriver
from .tspDriver import TspMemberDriver


from util.configTypes import AgentTemplate

def normalize_driver_config(member_cfg: AgentTemplate | Mapping[str, Any]) -> MemberDriverConfig:
    if hasattr(member_cfg, "model_dump"):
        member_cfg = member_cfg.model_dump()

    driver_cfg = member_cfg.get("driver")
    if driver_cfg:
        return MemberDriverConfig(
            driver_type=driver_cfg["type"],
            options={k: v for k, v in driver_cfg.items() if k != "type"},
        )

    runtime_cfg = member_cfg.get("runtime")
    if runtime_cfg:
        return MemberDriverConfig(
            driver_type=runtime_cfg["type"],
            options={k: v for k, v in runtime_cfg.items() if k != "type"},
        )

    if member_cfg.get("use_agent_sdk", False):
        return MemberDriverConfig(
            driver_type="claude_sdk",
            options={"allowed_tools": member_cfg.get("allowed_tools", [])},
        )

    return MemberDriverConfig(driver_type="native")


def build_member_driver(host, driver_config: MemberDriverConfig):
    if driver_config.driver_type == "native":
        return NativeMemberDriver(host, driver_config)
    if driver_config.driver_type == "claude_sdk":
        return ClaudeSdkMemberDriver(host, driver_config)
    if driver_config.driver_type == "tsp":
        return TspMemberDriver(host, driver_config)
    raise ValueError(f"未知 member driver 类型: {driver_config.driver_type}")
