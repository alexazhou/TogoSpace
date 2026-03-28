from pydantic import BaseModel

from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtAgentManager
from util import assertUtil


class AgentUpdateItem(BaseModel):
    id: int
    name: str
    role_template_name: str
    model: str = ""
    driver: str = "{}"


class SetAgentsRequest(BaseModel):
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
                "role_template_name": a.role_template_name,
                "employ_status": a.employ_status.name if a.employ_status else None,
                "model": a.model,
                "driver": a.driver,
            }
            for a in agents
        ]
        self.return_json({"agents": data})

    async def put(self):
        """PUT /agents/list.json?team_id=<id> - 批量更新成员配置"""
        team_id_raw = self.get_query_argument("team_id", None)
        if not team_id_raw:
            self.return_error(error_message="team_id is required", error_code="missing_team_id")
            return

        team_id = int(team_id_raw)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(SetAgentsRequest)

        # 检查所有 agent id 是否存在
        agent_ids = [item.id for item in request.agents]
        existing_agents = await gtAgentManager.get_agents_by_ids(agent_ids)
        assertUtil.assertEqual(len(existing_agents), len(agent_ids), f"input {len(agent_ids)} agent ids, but only found {len(existing_agents)} existed")

        # 执行更新
        for item in request.agents:
            await gtAgentManager.update_agent(
                agent_id=item.id,
                name=item.name,
                role_template_name=item.role_template_name,
                model=item.model,
                driver=item.driver,
            )

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
            "role_template_name": agent.role_template_name,
            "employ_status": agent.employ_status.name if agent.employ_status else None,
            "model": agent.model,
            "driver": agent.driver,
        })