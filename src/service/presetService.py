from __future__ import annotations

import logging

from constants import EmployStatus, RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoleTemplateManager, gtTeamManager
from exception import TeamAgentException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import agentService, deptService, roleTemplateService, roomService
from util import configUtil
from util.configTypes import DeptNodeConfig, TeamConfig, TeamRoomConfig

logger = logging.getLogger(__name__)


async def startup() -> None:
    return None


async def import_role_templates_from_app_config() -> None:
    for template in configUtil.get_app_config().role_templates:
        await roleTemplateService.import_role_template(
            name=template.name,
            soul=template.soul,
            model=template.model,
            driver=template.driver,
            allowed_tools=template.allowed_tools,
        )
    db_templates = await gtRoleTemplateManager.get_all_role_templates()
    logger.info(f"加载角色模版: {[t.name for t in db_templates]}")


async def _to_dept_tree_node(team_id: int, node: DeptNodeConfig) -> deptService.DeptTreeNode:
    lookup_names = list(dict.fromkeys([*node.members, node.manager]))
    member_rows = await gtAgentManager.get_team_agents_by_names(
        team_id,
        lookup_names,
        include_special=False,
    )
    member_id_map = {member.name: member.id for member in member_rows}
    missing_names = [name for name in lookup_names if name not in member_id_map]
    if missing_names:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的成员 '{missing_names[0]}' 在 team_members 中不存在",
            error_code="DEPT_MEMBER_NOT_FOUND",
        )

    return deptService.DeptTreeNode(
        dept_name=node.dept_name,
        dept_responsibility=node.dept_responsibility,
        manager_id=member_id_map[node.manager],
        member_ids=[member_id_map[name] for name in node.members],
        children=[await _to_dept_tree_node(team_id, child) for child in node.children],
    )


def _infer_room_type(members: list[str]) -> RoomType:
    ai_count = len([member for member in members if SpecialAgent.value_of(member) != SpecialAgent.OPERATOR])
    if any(SpecialAgent.value_of(member) == SpecialAgent.OPERATOR for member in members) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


async def _to_gt_room(team_id: int, room_config: TeamRoomConfig) -> GtRoom:
    member_ids = [
        agent.id
        for agent in await gtAgentManager.get_team_agents_by_names(
            team_id,
            room_config.members,
            include_special=True,
        )
    ]
    return GtRoom(
        id=room_config.id,
        team_id=team_id,
        name=room_config.name,
        type=_infer_room_type(room_config.members),
        initial_topic=room_config.initial_topic,
        max_turns=roomService.resolve_room_max_turns(room_config.max_turns),
        agent_ids=member_ids,
        biz_id=room_config.biz_id,
        tags=list(room_config.tags),
    )


async def _to_gt_agents(team_id: int, team_config: TeamConfig) -> list[GtAgent]:
    agents: list[GtAgent] = []
    for member in team_config.members:
        role_template = await gtRoleTemplateManager.get_role_template_by_name(member.role_template)
        if role_template is None:
            logger.warning(
                "跳过 Agent '%s'：未找到角色模板 '%s'",
                member.name,
                member.role_template,
            )
            continue

        agents.append(GtAgent(
            team_id=team_id,
            name=member.name,
            role_template_id=role_template.id,
            employ_status=EmployStatus.ON_BOARD,
            model=member.model or "",
            driver=member.driver,
        ))
    return agents


async def import_team_from_config(team_config: TeamConfig):
    existing = await gtTeamManager.get_team(team_config.name)
    if existing is not None:
        logger.info("Team '%s' 已存在，跳过导入", team_config.name)
        return None

    team = await gtTeamManager.save_team(GtTeam(
        name=team_config.name,
        config=team_config.config or {},
        max_function_calls=team_config.max_function_calls if team_config.max_function_calls is not None else 5,
        enabled=1,
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
    logger.info("Team '%s' 已从配置导入数据库", team_config.name)
    return team


async def import_teams_from_app_config() -> None:
    for team_config in configUtil.get_app_config().teams:
        team = await import_team_from_config(team_config)
        if team is None:
            logger.info("Team '%s' 已存在，跳过整组 preset 导入", team_config.name)
            continue

        if not team_config.dept_tree:
            logger.warning(f"Team '{team_config.name}' 缺少 dept_tree 配置，跳过导入")
            continue

        await deptService.import_dept_tree(team.id, await _to_dept_tree_node(team.id, team_config.dept_tree))

    logger.info("Team 配置已导入数据库")


async def import_from_app_config() -> None:
    await import_role_templates_from_app_config()
    await import_teams_from_app_config()


async def shutdown() -> None:
    return None
