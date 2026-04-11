from __future__ import annotations

import logging

from dal.db import gtTeamManager, gtAgentManager, gtAgentTaskManager, gtAgentHistoryManager, gtRoomMessageManager, gtRoomManager
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


async def stop_team_runtime(team_id: int) -> None:
    """停止指定 Team 的运行时（调度、Agent、Room）。"""
    schedulerService.stop_scheduler_team(team_id)
    await agentService.unload_team(team_id)
    await roomService.close_team_rooms(team_id)


async def restore_team(
    team_id: int,
    *,
    workspace_root: str | None = None,
    running_task_error_message: str = "task interrupted by team runtime restart",
) -> None:
    """从数据库恢复指定 Team 的运行时。"""
    team = await gtTeamManager.get_team_by_id(team_id)
    if team is None:
        logger.warning(f"恢复 Team 运行时失败: Team ID '{team_id}' 不存在")
        return
    if not team.enabled:
        logger.info("跳过恢复已停用 Team 的运行时: team=%s", team.name)
        return

    await agentService.load_team_agents(team.id, workspace_root=workspace_root)
    await roomService.load_team_rooms(team.id)
    await agentService.restore_team_agents_runtime_state(
        team.id,
        running_task_error_message=running_task_error_message,
    )
    await roomService.restore_team_rooms_runtime_state(team.id)
    await schedulerService.start_scheduling(team.name)
    logger.info("Team '%s' 运行时恢复完成", team.name)


async def restart_team_runtime(
    team_id: int,
    *,
    workspace_root: str | None = None,
    running_task_error_message: str = "task interrupted by team runtime restart",
) -> None:
    """重启指定 Team 的运行时。"""
    await stop_team_runtime(team_id)
    await restore_team(
        team_id,
        workspace_root=workspace_root,
        running_task_error_message=running_task_error_message,
    )


async def hot_reload_team(name: str) -> None:
    """触发指定 Team 的热更新。"""
    team = await gtTeamManager.get_team(name)
    if team is None:
        logger.warning(f"热更新失败: Team '{name}' 不存在")
        return

    await restart_team_runtime(team.id)
    logger.info("Team '%s' 热更新后已触发调度启动", name)

    logger.info(f"Team '{name}' 热更新完成")


async def create_team(
    name: str,
    config: dict | None = None,
    agents: list[GtAgent] | None = None,
    dept_tree: GtDept | None = None,
    preset_rooms: list[GtRoom] | None = None,
) -> int:
    """创建新 Team，并恢复其运行时。"""

    if await gtTeamManager.team_exists(name):
        raise TeamAgentException(f"Team '{name}' already exists", error_code="TEAM_EXISTS")

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

    if preset_rooms:
        await roomService.overwrite_team_rooms(team_id, preset_rooms)

    await restore_team(team_id)

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
    """删除 Team 配置并停止对应运行时。"""
    team = await gtTeamManager.get_team(name)
    if team is not None:
        await stop_team_runtime(team.id)

    await gtTeamManager.delete_team(name)

    logger.info(f"Team '{name}' 已删除")


async def set_team_enabled(team_id: int, enabled: bool) -> None:
    """设置 Team 的启用状态。"""
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

    await gtTeamManager.set_team_enabled(team_id, enabled)

    team_name = team.name
    if enabled:
        await restore_team(team_id)
    else:
        await stop_team_runtime(team_id)

    logger.info(f"Team '{team_name}' {'已启用' if enabled else '已停用'}")


async def clear_team_data(team_id: int) -> dict[str, int]:
    """清空团队运行数据（消息、历史、任务）。

    保留团队配置（成员、房间结构、部门结构）。

    Returns:
        删除统计 {"tasks": n, "histories": n, "messages": n}
    """
    # 1. 停止运行时
    await stop_team_runtime(team_id)

    # 2. 清空数据库（按依赖顺序）
    tasks_deleted = await gtAgentTaskManager.delete_tasks_by_team(team_id)
    histories_deleted = await gtAgentHistoryManager.delete_history_by_team(team_id)
    messages_deleted = await gtRoomMessageManager.delete_messages_by_team(team_id)

    # 3. 重置房间的 agent_read_index
    await gtRoomManager.reset_room_read_index(team_id)

    result = {
        "tasks": tasks_deleted,
        "histories": histories_deleted,
        "messages": messages_deleted,
    }

    logger.info(f"Team ID={team_id} 数据已清空: {result}")
    return result
