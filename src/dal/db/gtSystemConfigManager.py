from __future__ import annotations

import os

from constants import SystemConfigKey
from model.dbModel.gtSystemConfig import GtSystemConfig


async def get_config(key: SystemConfigKey) -> str | None:
    """获取指定配置项的值。"""
    row = await GtSystemConfig.aio_get_or_none(GtSystemConfig.key == key)
    return row.value if row else None


async def set_config(key: SystemConfigKey, value: str) -> None:
    """设置指定配置项的值。"""
    await (
        GtSystemConfig.insert(key=key, value=value)
        .on_conflict(
            conflict_target=[GtSystemConfig.key],
            update={
                GtSystemConfig.value: value,
                GtSystemConfig.updated_at: GtSystemConfig._now(),
            },
        )
        .aio_execute()
    )


async def get_working_directory() -> str:
    """获取系统工作目录，若未配置则返回仓库根目录。"""
    value = await get_config(SystemConfigKey.WORKING_DIRECTORY)
    if value:
        return value
    # 默认返回仓库根目录
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))


async def get_team_working_directory(team_name: str) -> str:
    """获取指定 team 的工作目录（系统工作目录/team_name）。"""
    base_dir = await get_working_directory()
    return os.path.join(base_dir, team_name)


__all__ = ["get_config", "set_config", "get_working_directory", "get_team_working_directory"]