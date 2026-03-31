from __future__ import annotations

import logging

from constants import DriverType, EmployStatus
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager
from exception import TeamAgentException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import deptService, roomService, schedulerService, agentService
from util import assertUtil

logger = logging.getLogger(__name__)


async def _build_team_member_rows(team_id: int, members: list[GtAgent]) -> list[GtAgent]:
    existing_agents = await gtAgentManager.get_team_agents(team_id)
    existing_by_name = {agent.name: agent for agent in existing_agents}

    template_ids = sorted(
        {
            member.role_template_id
            for member in members
            if isinstance(member.role_template_id, int)
        }
    )
    templates = await gtRoleTemplateManager.get_role_templates_by_ids(template_ids)
    valid_template_ids = {template.id for template in templates}
    missing_template_ids = sorted(set(template_ids) - valid_template_ids)
    if missing_template_ids:
        raise TeamAgentException(
            error_message=f"角色模板不存在: {missing_template_ids}",
            error_code="role_template_not_found",
        )

    agent_rows: list[GtAgent] = []
    for member in members:
        role_template_id = member.role_template_id
        name = member.name
        existing = existing_by_name.get(name)
        model = member.model or ""
        driver = member.driver

        if existing is None:
            agent_rows.append(
                GtAgent(
                    team_id=team_id,
                    name=name,
                    role_template_id=role_template_id,
                    employ_status=EmployStatus.ON_BOARD,
                    model=model,
                    driver=driver,
                )
            )
            continue

        existing.role_template_id = role_template_id
        existing.employ_status = EmployStatus.ON_BOARD
        existing.model = model
        existing.driver = driver
        agent_rows.append(existing)

    return agent_rows


async def startup() -> None:
    return None


async def create_team(
    name: str,
    config: dict | None = None,
    max_function_calls: int | None = None,
    members: list[GtAgent] | None = None,
    dept_tree: deptService.DeptTreeNode | None = None,
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
        max_function_calls=max_function_calls if max_function_calls is not None else 5,
        enabled=1,
        deleted=0,
    ))
    team_id = team.id
    await agentService.overwrite_team_agents(team_id, members or [])

    if dept_tree:
        await deptService.import_dept_tree(team_id, dept_tree)

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


async def update_team_members(team_id: int, members: list[GtAgent]) -> None:
    await gtAgentManager.batch_save_agents(team_id, await _build_team_member_rows(team_id, members))


async def delete_team(name: str) -> None:
    """删除 Team 配置并触发热更新。"""
    team = await gtTeamManager.get_team(name)
    if team is not None:
        await roomService.close_team_rooms(team.id)
    schedulerService.stop_team(name)

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
        schedulerService.stop_team(team_name)

    logger.info(f"Team '{team_name}' {'已启用' if enabled else '已停用'}")


async def hot_reload_team(name: str) -> None:
    """触发指定 Team 的热更新。"""

    # 先停掉该 team 的调度任务，避免旧实例在热更新过程中继续消费事件
    schedulerService.stop_team(name)

    # 刷新成员实例，保证新增/变更成员可被调度命中
    await agentService.reload_team_agents_from_db(name)

    # 刷新调度器配置
    await schedulerService.refresh_team_config(name)

    # 刷新聊天室配置
    team = await gtTeamManager.get_team(name)
    if team is None:
        logger.warning(f"热更新失败: Team '{name}' 不存在")
        return
    await roomService.refresh_rooms_for_team(team.id)
    activated = await roomService.exit_init_rooms(name)
    logger.info("Team '%s' 热更新后退出 INIT 房间数=%s", name, activated)

    logger.info(f"Team '{name}' 热更新完成")
