from typing import Optional
from pydantic import BaseModel

from constants import DriverType
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtAgentManager
from service import teamService
from util import assertUtil


class MemberSaveItem(BaseModel):
    """成员保存项：id 可选，有则更新，无则创建。"""
    id: Optional[int] = None
    name: str
    role_template_name: str
    model: str = ""
    driver: DriverType = DriverType.NATIVE


class MembersSaveRequest(BaseModel):
    """全量覆盖成员列表请求。"""
    members: list[MemberSaveItem]


class AgentUpdateItem(BaseModel):
    id: int
    name: str
    role_template_name: str
    model: str = ""
    driver: DriverType = DriverType.NATIVE


class AgentBatchUpdateRequest(BaseModel):
    agents: list[AgentUpdateItem]


class AgentListHandler(BaseHandler):
    """GET /agents/list.json?team_id=<id> - 获取 team 的成员配置列表"""

    async def get(self):
        team_id_raw = self.get_query_argument("team_id", None)
        if not team_id_raw:
            self.return_json({"agents": []})
            return

        team_id = int(team_id_raw)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        agents = await gtAgentManager.get_agents_by_team(team_id)
        data = [
            {
                "id": a.id,
                "name": a.name,
                "employee_number": a.employee_number,
                "role_template_name": a.role_template_name,
                "employ_status": a.employ_status.name if a.employ_status else None,
                "model": a.model,
                "driver": a.driver.value if a.driver else None,
            }
            for a in agents
        ]
        self.return_json({"agents": data})


class TeamMembersSaveHandler(BaseHandler):
    """PUT /teams/<id>/members/save.json - 全量覆盖成员列表"""

    async def put(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(MembersSaveRequest)

        # 收集请求中有 id 的成员 ID
        request_ids = [m.id for m in request.members if m.id is not None]

        # 获取当前 team 所有成员
        existing_agents = await gtAgentManager.get_agents_by_team(team_id)
        existing_ids = {a.id for a in existing_agents}

        # 校验：请求中有 id 的成员必须存在于当前 team
        invalid_ids = [id_ for id_ in request_ids if id_ not in existing_ids]
        assertUtil.assertEqual(
            len(invalid_ids), 0,
            error_message=f"成员 ID 不存在于当前 team: {invalid_ids}",
            error_code="member_not_found",
        )

        # 校验：最终成员 name 在请求内必须唯一
        final_names = [m.name for m in request.members]
        duplicate_names = [n for n in final_names if final_names.count(n) > 1]
        assertUtil.assertEqual(
            len(duplicate_names), 0,
            error_message=f"成员 name 重复: {duplicate_names}",
            error_code="duplicate_member_name",
        )

        # 执行全量覆盖
        updated_members = await gtAgentManager.save_members_full_replace(team_id, request.members)

        # 触发热更新
        await teamService.hot_reload_team(team.name)

        # 返回最新完整成员列表
        self.return_json({
            "status": "ok",
            "members": [
                {
                    "id": m.id,
                    "name": m.name,
                    "employee_number": m.employee_number,
                    "role_template_name": m.role_template_name,
                    "model": m.model,
                    "driver": m.driver.value,
                }
                for m in updated_members
            ],
        })


class AgentBatchUpdateHandler(BaseHandler):
    """PUT /teams/<id>/agents/batch_update.json - 兼容旧批量更新接口"""

    async def put(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(AgentBatchUpdateRequest)

        agent_ids = [item.id for item in request.agents]
        existing_agents = await gtAgentManager.get_agents_by_ids(agent_ids)
        assertUtil.assertEqual(
            len(existing_agents),
            len(agent_ids),
            error_message=f"input {len(agent_ids)} agent ids, but only found {len(existing_agents)} existed",
            error_code="agent_not_found",
        )

        for item in request.agents:
            await gtAgentManager.update_agent(
                agent_id=item.id,
                name=item.name,
                role_template_name=item.role_template_name,
                model=item.model,
                driver=item.driver,
            )

        await teamService.hot_reload_team(team.name)
        self.return_json({"status": "ok"})


class AgentDetailHandler(BaseHandler):
    """GET /teams/<id>/agents/<name>.json - 获取单个成员配置详情"""

    async def get(self, team_id_str: str, agent_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        agent = await gtAgentManager.get_agent(team_id, agent_name)
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent '{agent_name}' not found in team '{team.name}'",
            error_code="agent_not_found",
        )

        self.return_json({
            "id": agent.id,
            "name": agent.name,
            "employee_number": agent.employee_number,
            "role_template_name": agent.role_template_name,
            "employ_status": agent.employ_status.name if agent.employ_status else None,
            "model": agent.model,
            "driver": agent.driver,
        })
