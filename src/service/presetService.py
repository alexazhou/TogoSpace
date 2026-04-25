from __future__ import annotations

import logging

from constants import EmployStatus, RoleTemplateType, RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoleTemplateManager, gtTeamManager
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service import agentService, deptService, roleTemplateService, roomService
from util import configUtil, i18nUtil
from util.configTypes import DeptNodeConfig, TeamConfig, TeamRoomConfig

logger = logging.getLogger(__name__)


async def startup() -> None:
    return None


async def _import_role_templates_from_app_config() -> None:
    for template in configUtil.get_app_config().role_templates:
        await roleTemplateService.save_role_template(GtRoleTemplate(
            name=template.name,
            soul=template.soul,
            model=template.model,
            type=RoleTemplateType.SYSTEM,
            allowed_tools=template.allowed_tools,
            i18n=template.i18n or {},
        ))
    db_templates = await gtRoleTemplateManager.get_all_role_templates()
    logger.info(f"加载角色模版: {[t.name for t in db_templates]}")


def _validate_dept_tree_config(node: DeptNodeConfig) -> None:
    """在进入数据库事务前，对部门树配置进行纯逻辑校验。"""
    for child in node.children:
        if child.manager not in node.agents:
            raise TogoException(
                f"部门 '{node.dept_name}' 的子部门 '{child.dept_name}' 的负责人 '{child.manager}' 不在父部门的 agents 列表中",
                error_code="CHILD_MANAGER_NOT_IN_PARENT_AGENTS",
            )
        _validate_dept_tree_config(child)


async def _to_dept_tree_node(team_id: int, node: DeptNodeConfig) -> GtDept:
    lookup_names = list(dict.fromkeys([*node.agents, node.manager]))
    gt_agents = await gtAgentManager.get_team_agents_by_names(
        team_id,
        lookup_names,
        include_special=False,
    )
    agent_id_map = {agent.name: agent.id for agent in gt_agents}
    missing_names = [name for name in lookup_names if name not in agent_id_map]
    if missing_names:
        raise TogoException(
            f"部门 '{node.dept_name}' 的 Agent '{missing_names[0]}' 在 team_agents 中不存在",
            error_code="DEPT_AGENT_NOT_FOUND",
        )

    return GtDept(
        name=node.dept_name,  # 存储稳定标识；display_name 从 i18n 解析
        responsibility=node.responsibility,  # 存储稳定标识；display responsibility 从 i18n 解析
        manager_id=agent_id_map[node.manager],
        agent_ids=[agent_id_map[name] for name in node.agents],
        i18n=node.i18n or {},  # 传递原始 i18n 字典
        children=[await _to_dept_tree_node(team_id, child) for child in node.children],
    )


def _infer_room_type(agent_names: list[str]) -> RoomType:
    ai_count = len([agent_name for agent_name in agent_names if SpecialAgent.value_of(agent_name) != SpecialAgent.OPERATOR])
    if any(SpecialAgent.value_of(agent_name) == SpecialAgent.OPERATOR for agent_name in agent_names) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


async def _to_gt_room(team_id: int, room_config: TeamRoomConfig) -> GtRoom:
    agent_ids = [
        agent.id
        for agent in await gtAgentManager.get_team_agents_by_names(
            team_id,
            room_config.agents,
            include_special=True,
        )
    ]
    # 使用稳定 name 作为 DB name；initial_topic 可从 i18n 按语言解析
    initial_topic = room_config.initial_topic

    if room_config.i18n and "initial_topic" in room_config.i18n:
        lang = configUtil.get_language()
        initial_topic = i18nUtil.extract_i18n_str(room_config.i18n.get("initial_topic"), default=initial_topic, lang=lang)
    return GtRoom(
        id=room_config.id,
        team_id=team_id,
        name=room_config.name,  # 存储稳定 ID；display_name 从 i18n 解析
        type=_infer_room_type(room_config.agents),
        initial_topic=initial_topic,
        max_turns=roomService.resolve_room_max_turns(room_config.max_turns),
        agent_ids=agent_ids,
        biz_id=room_config.biz_id,
        tags=list(room_config.tags),
        i18n=room_config.i18n or {},
    )


async def _to_gt_agents(team_id: int, team_config: TeamConfig) -> list[GtAgent]:
    agents: list[GtAgent] = []
    for agent in team_config.agents:
        role_template = await gtRoleTemplateManager.get_role_template_by_name(agent.role_template)
        if role_template is None:
            logger.warning(
                "跳过 Agent '%s'：未找到角色模板 '%s'",
                agent.name,
                agent.role_template,
            )
            continue

        agent_name = agent.name  # 存储稳定 ID；display_name 从 i18n 解析
        agents.append(GtAgent(
            team_id=team_id,
            name=agent_name,
            role_template_id=role_template.id,
            employ_status=EmployStatus.ON_BOARD,
            model=agent.model or "",
            driver=agent.driver,
            i18n=agent.i18n or {},
        ))
    return agents


async def _import_team_from_config(team_config: TeamConfig) -> GtTeam | None:
    # UUID 优先去重（含已删除）；无 UUID 时按 stable name 匹配（向后兼容旧格式）
    existing: GtTeam | None = None
    if team_config.uuid:
        existing = await gtTeamManager.get_team_by_uuid(team_config.uuid, include_deleted=True)
    if existing is None:
        existing = await gtTeamManager.get_team(team_config.name)
    if existing is not None:
        logger.info("Team '%s' 已存在（或已删除），跳过导入", team_config.name)
        return None

    # 1. 预校验：在写入任何数据前确保配置逻辑正确
    if team_config.dept_tree is not None:
        _validate_dept_tree_config(team_config.dept_tree)

    # 2. 保存团队主体（先创建停用状态，导入完成后再启用）
    team = await gtTeamManager.save_team(GtTeam(
        name=team_config.name,  # 存储稳定 ID；display_name 从 i18n 解析
        uuid=team_config.uuid,
        config=team_config.config or {},
        i18n=team_config.i18n or {},
        enabled=False,  # 导入期间必须停用
        deleted=0,
    ))

    await agentService.overwrite_team_agents(
        team.id,
        await _to_gt_agents(team.id, team_config),
    )
    await roomService.create_team_rooms(
        team.id,
        [await _to_gt_room(team.id, room) for room in team_config.preset_rooms],
    )
    if team_config.dept_tree is not None:
        await deptService.overwrite_dept_tree(team.id, await _to_dept_tree_node(team.id, team_config.dept_tree))

    # 导入完成后，根据 auto_start 决定是否启用团队
    if team_config.auto_start:
        await gtTeamManager.set_team_enabled(team.id, True)

    logger.info("Team '%s' 已从配置导入数据库", team_config.name)
    return team


async def _import_teams_from_app_config() -> None:
    # is_default=True 的团队优先导入（确保排在列表第一位）
    teams_config = list(configUtil.get_app_config().teams)
    teams_config.sort(key=lambda t: not t.is_default)

    for team_config in teams_config:
        team = await _import_team_from_config(team_config)
        if team is None:
            logger.info("Team '%s' 已存在，跳过整组 preset 导入", team_config.name)
            continue

    logger.info("Team 配置已导入数据库")


async def import_from_app_config() -> None:
    await _import_role_templates_from_app_config()
    await _import_teams_from_app_config()


async def shutdown() -> None:
    return None
