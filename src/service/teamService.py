from __future__ import annotations

import glob
import json
import logging
import os
from typing import List

from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager
from constants import RoomType
from exception import TeamAgentException

logger = logging.getLogger(__name__)


async def startup(config_dir: str = None) -> list:
    """启动时加载 Team 配置：
    1. 从数据库加载现有配置
    2. 从 JSON 文件扫描新配置，导入数据库
    3. 为没有 max_turns 的 room 设置默认值 100
    4. 返回最终的 Team 配置列表（兼容现有格式）
    """
    # 1. 扫描 JSON 文件
    if config_dir is None:
        config_dir = os.path.join(os.path.dirname(__file__), "../../config")
    teams_dir = os.path.join(config_dir, "teams")
    json_teams = {}
    for path in sorted(glob.glob(os.path.join(teams_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            team_config = json.load(f)
            name = team_config["name"]
            json_teams[name] = team_config
            logger.info(f"扫描到 Team 配置文件: {path}")

    # 2. 将 JSON 配置导入数据库（仅当不存在时）
    for name, team_config in json_teams.items():
        # 为没有 max_turns 的 room 设置默认值 100
        for room in team_config.get("rooms", []):
            if "max_turns" not in room:
                room["max_turns"] = 100
                logger.info(f"为 Team '{name}' 的 Room '{room['name']}' 设置默认 max_turns=100")

        await gtTeamManager.import_team_from_json(team_config)

    # 3. 从数据库加载所有配置
    team_configs = await gtTeamManager.get_all_team_configs()

    logger.info(f"从数据库加载了 {len(team_configs)} 个 Team 配置")
    return team_configs


async def reload_from_db() -> list:
    """从数据库重新加载配置。"""
    return await gtTeamManager.get_all_team_configs()


async def create_team(team_config: dict) -> None:
    """创建新 Team（自动触发热更新）。"""
    name = team_config["name"]

    # 检查 Team 是否已存在
    if await gtTeamManager.team_exists(name):
        raise TeamAgentException(f"Team '{name}' already exists", error_code="TEAM_EXISTS")

    # 创建 Team
    team = await gtTeamManager.upsert_team(team_config)
    team_id = team.id

    # 创建 Rooms（rooms 参数）
    rooms = team_config.get("rooms", [])
    for room in rooms:
        if "max_turns" not in room:
            room["max_turns"] = 100

    await gtRoomManager.upsert_rooms(team_id, rooms)

    # 创建 Members
    for room in rooms:
        room_name = room["name"]
        room_config = await gtRoomManager.get_room_config(team_id, room_name)
        if room_config:
            members = room.get("members", [])
            await gtRoomMemberManager.upsert_room_members(room_config.id, members)

    # 触发热更新
    await hot_reload_team(name)

    logger.info(f"Team '{name}' 已创建")


async def update_team(team_config: dict) -> None:
    """更新 Team 配置并触发热更新。"""
    name = team_config["name"]

    # 更新 Team 基本信息
    team = await gtTeamManager.upsert_team(team_config)
    team_id = team.id

    # 更新 Rooms（rooms 参数名保持兼容）
    rooms = team_config.get("rooms", [])
    for room in rooms:
        if "max_turns" not in room:
            room["max_turns"] = 100

    await gtRoomManager.upsert_rooms(team_id, rooms)

    # 更新 Members
    for room in rooms:
        room_name = room["name"]
        room_config = await gtRoomManager.get_room_config(team_id, room_name)
        if room_config:
            members = room.get("members", [])
            await gtRoomMemberManager.upsert_room_members(room_config.id, members)

    logger.info(f"Team '{name}' 配置已更新")

    # 触发热更新
    await hot_reload_team(name)


async def delete_team(name: str) -> None:
    """删除 Team 配置并触发热更新。"""
    from service import roomService, schedulerService

    await roomService.close_team_rooms(name)
    schedulerService.stop_team(name)

    # 软删除 Team
    await gtTeamManager.delete_team(name)

    logger.info(f"Team '{name}' 已删除")


async def hot_reload_team(name: str) -> None:
    """触发指定 Team 的热更新。"""
    from service import roomService, schedulerService

    # 重新加载配置
    team_configs = await reload_from_db()
    target_config = next((c for c in team_configs if c["name"] == name), None)

    if target_config is None:
        logger.warning(f"热更新失败: Team '{name}' 不存在")
        return

    # 刷新调度器配置
    schedulerService.refresh_team_config(name, team_configs)

    # 刷新聊天室配置
    await roomService.refresh_rooms_for_team(name, team_configs)

    logger.info(f"Team '{name}' 热更新完成")