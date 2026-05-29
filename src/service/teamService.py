from __future__ import annotations

import logging

from dal.db import gtTeamManager, gtAgentManager, gtScheculeTaskManager, gtAgentHistoryManager, gtRoomMessageManager, gtRoomManager, gtAgentTaskManager, gtAgentActivityManager
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import deptService, roomService, schedulerService, agentService
from util import assertUtil, fileUtil

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

    await _sync_team_agent_status_with_dept_tree(team.id)
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

    from service import messageBus
    from constants import MessageBusTopic
    messageBus.publish(MessageBusTopic.TEAM_RELOADED, team_id=team.id)
    logger.info(f"Team '{name}' 热更新完成")


async def _sync_team_agent_status_with_dept_tree(team_id: int) -> None:
    """按当前部门树同步成员在岗状态，避免未入组织树的成员进入运行时。"""
    dept_root = await deptService.get_dept_tree(team_id)
    if dept_root is None:
        return

    on_board_agent_ids, _ = dept_root.collect_dept_and_agent_ids()
    await agentService.overwrite_team_agent_employ_status(team_id, on_board_agent_ids)


async def create_team(
    name: str,
    config: dict | None = None,
    agents: list[GtAgent] | None = None,
    dept_tree: GtDept | None = None,
    preset_rooms: list[GtRoom] | None = None,
    auto_start: bool = True,
) -> int:
    """创建新 Team，并恢复其运行时。

    Args:
        name: 团队名称
        config: 团队配置
        agents: Agent 列表
        dept_tree: 部门树
        preset_rooms: 预置房间
        auto_start: 是否自动启动（默认 True）
    """
    if await gtTeamManager.team_exists(name):
        raise TogoException(f"Team '{name}' already exists", error_code="TEAM_EXISTS")

    # 校验工作目录路径格式
    if config and config.get("working_directory"):
        workdir = config["working_directory"]
        fileUtil.validate_absolute_path(workdir)
        fileUtil.ensure_dir(workdir)

    # 先创建 disabled 状态，初始化完成后再启用
    team = await gtTeamManager.save_team(GtTeam(
        name=name,
        config=config or {},
        enabled=False,
        deleted=0,
    ))
    team_id = team.id

    # 初始化 agents 和 dept_tree（需要 team disabled 状态）
    await agentService.overwrite_team_agents(team_id, agents or [])

    if dept_tree:
        await deptService.overwrite_dept_tree(team_id, dept_tree)

    if preset_rooms:
        await roomService.overwrite_team_rooms(team_id, preset_rooms)

    # 根据参数决定是否启用
    if auto_start:
        await gtTeamManager.set_team_enabled(team_id, True)
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
            fileUtil.validate_absolute_path(working_directory)
            fileUtil.ensure_dir(working_directory)
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
    """设置 Team 的启用状态。

    如果状态未变化则跳过操作（避免重复加载/卸载）。
    """
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

    # 状态未变化时跳过
    if team.enabled == enabled:
        logger.info(f"Team '{team.name}' 已经处于 {'启用' if enabled else '停用'} 状态，跳过操作")
        return

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
    tasks_deleted = await gtScheculeTaskManager.delete_tasks_by_team(team_id)
    agent_tasks_deleted = await gtAgentTaskManager.delete_tasks_by_team(team_id)
    histories_deleted = await gtAgentHistoryManager.delete_history_by_team(team_id)
    messages_deleted = await gtRoomMessageManager.delete_messages_by_team(team_id)
    activities_deleted = await gtAgentActivityManager.delete_activities_by_team(team_id)

    # 3. 删除所有非 DEPT 房间（保留部门树管理的房间）
    all_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    non_dept_ids = [r.id for r in all_rooms if r.id is not None and "DEPT" not in (r.tags or [])]
    rooms_deleted = await gtRoomManager.delete_rooms_by_ids(non_dept_ids) if non_dept_ids else 0

    # 4. 重置存留 DEPT 房间的运行时状态（agent_read_index / speaker_index）
    #    消息删除后 seq 从 0 重新分配，若不重置读取位置，agent 的 read_index 仍指向旧
    #    位置（如 {7: 1, 8: 2}），导致初始系统消息（seq=0）被跳过，房间激活后无任何调度。
    await gtRoomManager.reset_room_runtime_state(team_id)

    result = {
        "tasks": tasks_deleted + agent_tasks_deleted,
        "histories": histories_deleted,
        "messages": messages_deleted,
        "rooms": rooms_deleted,
        "activities": activities_deleted,
    }

    logger.info(f"Team ID={team_id} 数据已清空: {result}")
    return result


async def clear_agent_data(agent_id: int) -> dict[str, int]:
    """清除指定 Agent 的历史记录。

    清除后触发 Agent 所在 Team 的热更新，重建运行态。

    Returns:
        删除统计 {"histories": n}
    """
    agent = await gtAgentManager.get_agent_by_id(agent_id)
    if agent is None:
        raise TogoException(f"Agent ID '{agent_id}' not found", "agent_not_found")

    team = await gtTeamManager.get_team_by_id(agent.team_id)
    if team is None:
        raise TogoException(f"Team ID '{agent.team_id}' not found for agent", "team_not_found")

    histories_deleted = await gtAgentHistoryManager.delete_history_by_agent(agent_id)

    result = {"histories": histories_deleted}
    logger.info("Agent ID=%d 历史数据已清除: %s", agent_id, result)

    await hot_reload_team(team.name)
    return result
