from __future__ import annotations

import os

from constants import SystemConfigKey
from dal.db import gtSystemConfigManager


def _default_working_directory() -> str:
    """返回仓库根目录作为默认工作目录。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


async def get_working_directory() -> str:
    """获取系统工作目录，若未配置则返回仓库根目录。"""
    value = await gtSystemConfigManager.get_config(SystemConfigKey.WORKING_DIRECTORY)
    if value:
        return value
    return _default_working_directory()


async def get_team_working_directory(team_name: str) -> str:
    """获取指定 team 的工作目录（系统工作目录/team_name）。"""
    base_dir = await get_working_directory()
    return os.path.join(base_dir, team_name)


__all__ = ["get_working_directory", "get_team_working_directory"]
