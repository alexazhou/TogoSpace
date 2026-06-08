from __future__ import annotations

from constants import EmployStatus, SpecialAgent
from dal.db import gtAgentManager, gtRoleTemplateManager, gtRoomManager, gtTeamManager
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from service import deptService
from util import assertUtil
from util.configTypes import AgentPreset, DeptNodePreset, RoleTemplatePreset, TeamPreset, TeamRoomPreset


def _resolve_agent_name(agent_id: int, agents_by_id: dict[int, GtAgent]) -> str:
    special_agent = SpecialAgent.value_of(agent_id)
    if special_agent is not None:
        return special_agent.name
    agent = agents_by_id.get(agent_id)
    if agent is None:
        raise TogoException(
            f"导出 Team preset 时未找到 Agent ID '{agent_id}' 对应的成员名称",
            error_code="TEAM_EXPORT_AGENT_NOT_FOUND",
        )
    return agent.name


def _to_dept_node_preset(node: GtDept, agents_by_id: dict[int, GtAgent]) -> DeptNodePreset:
    return DeptNodePreset(
        dept_name=node.name,
        responsibility=node.responsibility,
        manager=_resolve_agent_name(node.manager_id, agents_by_id),
        agents=[_resolve_agent_name(agent_id, agents_by_id) for agent_id in node.agent_ids],
        children=[_to_dept_node_preset(child, agents_by_id) for child in node.children],
        i18n=node.i18n or None,
    )


async def export_team_preset(team_id: int) -> dict[str, object]:
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

    agents = await gtAgentManager.get_team_all_agents(team_id, status=EmployStatus.ON_BOARD)
    agents_by_id = {
        agent.id: agent
        for agent in agents
        if agent.id is not None
    }
    role_templates = await gtRoleTemplateManager.get_role_templates_by_ids(
        list({agent.role_template_id for agent in agents}),
    )
    role_templates_by_id = {
        role_template.id: role_template
        for role_template in role_templates
        if role_template.id is not None
    }
    agent_presets: list[AgentPreset] = []
    for agent in agents:
        role_template = role_templates_by_id.get(agent.role_template_id)
        if role_template is None:
            raise TogoException(
                f"导出 Team preset 时未找到 Agent '{agent.name}' 对应的角色模板",
                error_code="TEAM_EXPORT_ROLE_TEMPLATE_NOT_FOUND",
            )
        agent_presets.append(AgentPreset(
            name=agent.name,
            i18n=agent.i18n or None,
            role_template=role_template.name,
            model=agent.model or None,
            driver=agent.driver,
            allow_tools=agent.allow_tools,
            allow_skills=agent.allow_skills,
        ))

    rooms = await gtRoomManager.get_rooms_by_team(team_id)
    dept_tree = await deptService.get_dept_tree(team_id)
    team_preset = TeamPreset(
        uuid=team.uuid,
        name=team.name,
        i18n=team.i18n or None,
        config=team.config or {},
        agents=agent_presets,
        dept_tree=_to_dept_node_preset(dept_tree, agents_by_id) if dept_tree is not None else None,
        preset_rooms=[
            TeamRoomPreset(
                name=room.name,
                i18n=room.i18n or None,
                agents=[_resolve_agent_name(agent_id, agents_by_id) for agent_id in room.agent_ids],
                initial_topic=room.initial_topic or "",
                max_rounds=room.max_rounds,
                biz_id=room.biz_id,
                tags=list(room.tags or []),
            )
            for room in rooms
            if not (room.biz_id or "").startswith("DEPT:")
        ],
        auto_start=bool(team.enabled),
    )
    export_data = team_preset.model_dump(mode="json", exclude_none=True)
    export_data["rule_templates"] = [
        RoleTemplatePreset(
            name=role_template.name,
            i18n=role_template.i18n or None,
            soul=role_template.soul,
        ).model_dump(mode="json", exclude_none=True)
        for role_template in role_templates
    ]
    return export_data
