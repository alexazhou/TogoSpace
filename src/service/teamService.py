from __future__ import annotations

import logging

from dal.db import gtTeamManager, gtAgentManager, gtRoomManager
from exception import TeamAgentException
from service import deptService
from util import configUtil
from util.configTypes import TeamConfig

logger = logging.getLogger(__name__)

_teams: list[TeamConfig] = []


def get_teams() -> list[TeamConfig]:
    return list(_teams)


async def startup() -> None:
    """启动时加载 Team 配置：
    1. 将 JSON 配置导入数据库（仅当不存在时）
    2. 为没有 max_turns 的 room 设置默认值 100
    3. 从数据库加载最终配置，缓存到模块状态
    4. 为已有 agents 分配工号（employee_number）
    """
    global _teams
    json_teams = configUtil.get_app_config().teams

    # 将 JSON 配置导入数据库（仅当不存在时）
    for team_config in json_teams:
        name = team_config.name
        # 为没有 max_turns 的 room 设置默认值 100
        for room in team_config.preset_rooms:
            if not room.max_turns:
                room.max_turns = 100
                logger.info(f"为 Team '{name}' 的 Room '{room.name}' 设置默认 max_turns=100")

        await gtTeamManager.import_team_from_config(team_config)

        if team_config.dept_tree:
            team = await gtTeamManager.get_team(name)
            if team is not None:
                await deptService.import_dept_tree(team.id, team_config.dept_tree)

    # 从数据库加载所有配置
    _teams = await gtTeamManager.get_all_team_configs()

    logger.info(f"从数据库加载了 {len(_teams)} 个 Team 配置")

    # 为已有 agents 分配工号（处理迁移前的数据）
    for team_config in _teams:
        team = await gtTeamManager.get_team(team_config.name)
        if team is not None:
            assigned = await gtAgentManager.assign_employee_numbers_for_existing_agents(team.id)
            if assigned > 0:
                logger.info(f"为 Team '{team_config.name}' 的 {assigned} 个 agents 分配了工号")


async def reload_from_db() -> list[TeamConfig]:
    """从数据库重新加载配置。"""
    global _teams
    _teams = await gtTeamManager.get_all_team_configs()
    return list(_teams)


async def create_team(team_config: TeamConfig) -> None:
    """创建新 Team（自动触发热更新）。"""
    name = team_config.name

    # 检查 Team 是否已存在
    if await gtTeamManager.team_exists(name):
        raise TeamAgentException(f"Team '{name}' already exists", error_code="TEAM_EXISTS")

    # 创建 Team
    team = await gtTeamManager.upsert_team(team_config)
    team_id = team.id
    await gtAgentManager.upsert_agents(team_id, team_config.members)

    if team_config.dept_tree:
        await deptService.import_dept_tree(team_id, team_config.dept_tree)

    # 创建 Rooms（rooms 参数）
    rooms = team_config.preset_rooms
    for room in rooms:
        if not room.max_turns:
            room.max_turns = 100

    await gtRoomManager.upsert_rooms(team_id, rooms)

    # 创建 Members
    for room in rooms:
        room_name = room.name
        room_config = await gtRoomManager.get_room_config(team_id, room_name)
        if room_config:
            members = room.members
            await gtRoomManager.upsert_room_members(room_config.id, members)

    # 触发热更新
    await hot_reload_team(name)

    logger.info(f"Team '{name}' 已创建")


async def update_team(team_config: TeamConfig) -> None:
    """更新 Team 配置并触发热更新。"""
    name = team_config.name

    # 更新 Team 基本信息
    team = await gtTeamManager.upsert_team(team_config)
    team_id = team.id
    await gtAgentManager.upsert_agents(team_id, team_config.members)

    # 更新 preset_rooms
    rooms = team_config.preset_rooms
    for room in rooms:
        if not room.max_turns:
            room.max_turns = 100

    await gtRoomManager.upsert_rooms(team_id, rooms)

    # 更新 Members
    for room in rooms:
        room_name = room.name
        room_config = await gtRoomManager.get_room_config(team_id, room_name)
        if room_config:
            members = room.members
            await gtRoomManager.upsert_room_members(room_config.id, members)

    logger.info(f"Team '{name}' 配置已更新")

    # 触发热更新
    await hot_reload_team(name)


async def delete_team(name: str) -> None:
    """删除 Team 配置并触发热更新。"""
    from service import roomService, schedulerService

    team = await gtTeamManager.get_team(name)
    if team is not None:
        await roomService.close_team_rooms(team.id)
    schedulerService.stop_team(name)

    # 软删除 Team
    await gtTeamManager.delete_team(name)

    logger.info(f"Team '{name}' 已删除")


async def hot_reload_team(name: str) -> None:
    """触发指定 Team 的热更新。"""
    from service import roomService, schedulerService, agentService

    # 重新加载配置
    team_configs = await reload_from_db()
    target_config = next((c for c in team_configs if c.name == name), None)

    if target_config is None:
        logger.warning(f"热更新失败: Team '{name}' 不存在")
        return

    # 先停掉该 team 的调度任务，避免旧实例在热更新过程中继续消费事件
    schedulerService.stop_team(name)

    # 刷新成员实例，保证新增/变更成员可被调度命中
    await agentService.reload_team_agents(name, team_configs)

    # 刷新调度器配置
    schedulerService.refresh_team_config(name, team_configs)

    # 刷新聊天室配置
    team = await gtTeamManager.get_team(name)
    if team is None:
        logger.warning(f"热更新失败: Team '{name}' 不存在")
        return
    await roomService.refresh_rooms_for_team(team.id, team_configs)
    activated = roomService.exit_init_rooms(name)
    logger.info("Team '%s' 热更新后退出 INIT 房间数=%s", name, activated)

    logger.info(f"Team '{name}' 热更新完成")
