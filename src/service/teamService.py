from __future__ import annotations

import logging

from constants import DriverType, EmployStatus
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager
from exception import TeamAgentException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from service import deptService, roomService, schedulerService, agentService
from util import configUtil, assertUtil
from util.configTypes import AgentConfig, TeamConfig

logger = logging.getLogger(__name__)

_teams: list[TeamConfig] = []


def get_teams() -> list[TeamConfig]:
    return list(_teams)


def _build_team_row(team_config: TeamConfig, existing: GtTeam | None = None) -> GtTeam:
    return GtTeam(
        id=existing.id if existing is not None else None,
        name=team_config.name,
        config=dict(team_config.config or {}),
        max_function_calls=team_config.max_function_calls if team_config.max_function_calls is not None else 5,
        enabled=existing.enabled if existing is not None else 1,
        deleted=existing.deleted if existing is not None else 0,
    )


async def _resolve_role_template_id(agent_config: AgentConfig) -> int:
    template = await gtRoleTemplateManager.get_role_template_by_name(agent_config.role_template)
    if template is None:
        return 0
    return template.id


async def _build_agent_rows(team_id: int, agent_configs: list[AgentConfig]) -> list[GtAgent]:
    existing_agents = await gtAgentManager.get_agents_by_team(team_id)
    existing_by_name = {agent.name: agent for agent in existing_agents}

    agent_rows: list[GtAgent] = []
    for agent_config in agent_configs:
        role_template_id = await _resolve_role_template_id(agent_config)
        if role_template_id == 0:
            logger.warning(
                "跳过 Agent '%s'：未找到角色模板 '%s'",
                agent_config.name,
                agent_config.role_template,
            )
            continue

        existing = existing_by_name.get(agent_config.name)
        if existing is None:
            agent_rows.append(
                GtAgent(
                    team_id=team_id,
                    name=agent_config.name,
                    role_template_id=role_template_id,
                    employ_status=EmployStatus.ON_BOARD,
                    model=agent_config.model or "",
                    driver=agent_config.driver,
                )
            )
            continue

        existing.role_template_id = role_template_id
        existing.employ_status = EmployStatus.ON_BOARD
        existing.model = agent_config.model or ""
        existing.driver = agent_config.driver
        agent_rows.append(existing)

    return agent_rows


async def get_team_config(name: str) -> TeamConfig | None:
    team = await gtTeamManager.get_team(name)
    if team is None:
        return None

    agent_rows = await gtAgentManager.get_agents_by_team(team.id)
    template_rows = await gtRoleTemplateManager.get_role_templates_by_ids(
        [agent.role_template_id for agent in agent_rows]
    )
    templates_by_id = {template.id: template for template in template_rows}

    members: list[AgentConfig] = []
    for agent in agent_rows:
        template = templates_by_id.get(agent.role_template_id)
        if template is None:
            logger.warning(
                "Agent '%s' 引用的角色模板不存在: role_template_id=%s",
                agent.name,
                agent.role_template_id,
            )
            continue
        members.append(
            AgentConfig(
                name=agent.name,
                role_template=template.template_name,
                model=agent.model or None,
                driver=agent.driver if isinstance(agent.driver, DriverType) else DriverType.NATIVE,
            )
        )

    return TeamConfig(
        name=team.name,
        config=team.get_config(),
        members=members,
        preset_rooms=await roomService.list_team_room_configs(team.id),
        max_function_calls=team.max_function_calls,
    )


async def get_all_team_configs_from_db() -> list[TeamConfig]:
    team_configs: list[TeamConfig] = []
    for team in await gtTeamManager.get_all_teams():
        config = await get_team_config(team.name)
        if config is not None:
            team_configs.append(config)
    return team_configs


async def import_team_from_config(team_config: TeamConfig) -> None:
    existing = await gtTeamManager.get_team(team_config.name)
    if existing is not None:
        logger.info("Team '%s' 已存在，跳过导入", team_config.name)
        return

    team = await gtTeamManager.save_team(_build_team_row(team_config))
    team_id = team.id

    await gtAgentManager.batch_save_agents(team_id, await _build_agent_rows(team_id, team_config.members))
    await roomService.save_team_rooms_from_config(team_id, team_config.preset_rooms)

    logger.info("Team '%s' 已从配置导入数据库", team_config.name)


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

        await import_team_from_config(team_config)

        team = await gtTeamManager.get_team(name)
        if team is None:
            logger.warning(f"Team '{name}' 导入失败，跳过")
            continue

        if not team_config.dept_tree:
            logger.warning(f"Team '{name}' 缺少 dept_tree 配置，跳过导入")
            continue

        await deptService.import_dept_tree(team.id, team_config.dept_tree)

    # 从数据库加载所有配置
    _teams = await get_all_team_configs_from_db()

    logger.info(f"从数据库加载了 {len(_teams)} 个 Team 配置")


async def reload_from_db() -> list[TeamConfig]:
    """从数据库重新加载配置。"""
    global _teams
    _teams = await get_all_team_configs_from_db()
    return list(_teams)


async def create_team(team_config: TeamConfig) -> int:
    """创建新 Team（自动触发热更新）。"""
    name = team_config.name

    # 检查 Team 是否已存在
    if await gtTeamManager.team_exists(name):
        raise TeamAgentException(f"Team '{name}' already exists", error_code="TEAM_EXISTS")

    # 创建 Team
    team = await gtTeamManager.save_team(_build_team_row(team_config))
    team_id = team.id
    await gtAgentManager.batch_save_agents(team_id, await _build_agent_rows(team_id, team_config.members))

    if team_config.dept_tree:
        await deptService.import_dept_tree(team_id, team_config.dept_tree)

    # 创建 Rooms（rooms 参数）
    rooms = team_config.preset_rooms
    for room in rooms:
        if not room.max_turns:
            room.max_turns = 100

    await roomService.save_team_rooms_from_config(team_id, rooms)

    # 触发热更新
    await hot_reload_team(name)

    logger.info(f"Team '{name}' 已创建")
    return team_id


async def update_team(team_config: TeamConfig) -> None:
    """更新 Team 配置并触发热更新。"""
    name = team_config.name

    # 更新 Team 基本信息
    existing = await gtTeamManager.get_team(name)
    team = await gtTeamManager.save_team(_build_team_row(team_config, existing))
    team_id = team.id
    await gtAgentManager.batch_save_agents(team_id, await _build_agent_rows(team_id, team_config.members))

    # 更新 preset_rooms
    rooms = team_config.preset_rooms
    for room in rooms:
        if not room.max_turns:
            room.max_turns = 100

    await roomService.save_team_rooms_from_config(team_id, rooms)

    logger.info(f"Team '{name}' 配置已更新")

    # 触发热更新
    await hot_reload_team(name)


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
    activated = await roomService.exit_init_rooms(name)
    logger.info("Team '%s' 热更新后退出 INIT 房间数=%s", name, activated)

    logger.info(f"Team '{name}' 热更新完成")
