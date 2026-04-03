from __future__ import annotations

import logging

from dal.db import gtTeamManager, gtAgentManager
from exception import TeamAgentException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import deptService, roomService, schedulerService, agentService
from util import assertUtil

logger = logging.getLogger(__name__)


async def startup() -> None:
    return None


async def create_team(
    name: str,
    config: dict | None = None,
    agents: list[GtAgent] | None = None,
    dept_tree: GtDept | None = None,
    preset_rooms: list[GtRoom] | None = None,
) -> int:
    """创建新 Team（自动触发热更新）。"""

    # 检查 Team 是否已存在
    if await gtTeamManager.team_exists(name):
        raise TeamAgentException(f"Team '{name}' already exists", error_code="TEAM_EXISTS")

    # 创建 Team
    team = await gtTeamManager.save_team(GtTeam(
        name=name,
        config=config or {},
        enabled=1,
        deleted=0,
    ))
    team_id = team.id
    await agentService.overwrite_team_agents(team_id, agents or [])

    if dept_tree:
        await deptService.overwrite_dept_tree(team_id, dept_tree)

    # 创建 Rooms（常规流程，不走”配置导入专用”接口）
    if preset_rooms:
        await roomService.overwrite_team_rooms(team_id, preset_rooms)

    # 触发热更新
    await hot_reload_team(name)

    logger.info(f"Team '{name}' 已创建")
    return team_id


async def update_team_base_info(team_id: int, working_directory: str | None = None, config_updates: dict | None = None) -> GtTeam:
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

    config = dict(team.config or {})
    if config_updates:
        config.update(config_updates)
    if working_directory is not None:
        if working_directory:
            config["working_directory"] = working_directory
        else:
            config.pop("working_directory", None)
    team.config = config
    return await gtTeamManager.save_team(team)


async def delete_team(name: str) -> None:
    """删除 Team 配置并触发热更新。"""
    team = await gtTeamManager.get_team(name)
    if team is not None:
        await roomService.close_team_rooms(team.id)
        schedulerService.stop_team(team.id)

    # 软删除 Team
    await gtTeamManager.delete_team(name)

    logger.info(f"Team '{name}' 已删除")


async def set_team_enabled(team_id: int, enabled: bool) -> None:
    """设置 Team 的启用状态。"""
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

    await gtTeamManager.set_team_enabled(team_id, enabled)

    team_name = team.name
    if enabled:
        # 启用时触发热更新
        await hot_reload_team(team_name)
    else:
        # 停用时停止调度
        schedulerService.stop_team(team_id)

    logger.info(f"Team '{team_name}' {'已启用' if enabled else '已停用'}")


async def hot_reload_team(name: str) -> None:
    """触发指定 Team 的热更新。"""
    team = await gtTeamManager.get_team(name)
    if team is None:
        logger.warning(f"热更新失败: Team '{name}' 不存在")
        return

    # 先停掉该 team 的调度任务，避免旧实例在热更新过程中继续消费事件
    schedulerService.stop_team(team.id)

    # 刷新成员实例，保证新增/变更成员可被调度命中
    await agentService.reload_team_agents_from_db(team.id)

    # 刷新聊天室配置
    await roomService.refresh_rooms_for_team(team.id)
    await schedulerService.start_scheduling(name)
    logger.info("Team '%s' 热更新后已触发调度启动", name)

    logger.info(f"Team '{name}' 热更新完成")
