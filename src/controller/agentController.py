from typing import Optional

from pydantic import BaseModel

from constants import DriverType, AgentStatus, AgentTaskStatus, SpecialAgent
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtAgentTaskManager
from model.dbModel.gtAgent import GtAgent
from service import teamService, agentService, roomService
from util import assertUtil


class AgentSaveItem(BaseModel):
    """Agent 保存项：id 可选，有则更新，无则创建。"""
    id: Optional[int] = None
    name: str
    role_template_id: int
    model: str = ""
    driver: DriverType = DriverType.NATIVE


class AgentsSaveRequest(BaseModel):
    """全量覆盖 Agent 列表请求。"""
    agents: list[AgentSaveItem]


class AgentUpdateItem(BaseModel):
    id: int
    name: str
    role_template_id: int
    model: str = ""
    driver: DriverType = DriverType.NATIVE


class AgentBatchUpdateRequest(BaseModel):
    agents: list[AgentUpdateItem]


async def _assert_role_templates_exist(template_ids: list[int]) -> None:
    gt_role_templates = await gtRoleTemplateManager.get_role_templates_by_ids(list(set(template_ids)))
    existing_ids = {template.id for template in gt_role_templates}
    missing_ids = sorted(set(template_ids) - existing_ids)
    assertUtil.assertEqual(
        len(missing_ids),
        0,
            error_message=f"角色模板不存在: {missing_ids}",
            error_code="role_template_not_found",
        )


async def _build_agent_detail_payload(agent: GtAgent) -> dict:
    team = await gtTeamManager.get_team_by_id(agent.team_id)
    runtime_status_map = agentService.get_team_runtime_status_map(agent.team_id)
    first_task = await gtAgentTaskManager.get_first_unfinish_task(agent.id)
    current_error_message = None
    if first_task is not None and first_task.status == AgentTaskStatus.FAILED:
        current_error_message = first_task.error_message
    return {
        "id": agent.id,
        "name": agent.name,
        "agent_name": agent.name,
        "employee_number": agent.employee_number,
        "role_template_id": agent.role_template_id,
        "team_id": agent.team_id,
        "team_name": team.name if team is not None else None,
        "status": runtime_status_map.get(agent.id, AgentStatus.IDLE).name,
        "employ_status": agent.employ_status.name if agent.employ_status else None,
        "model": agent.model,
        "driver": agent.driver.value if agent.driver else None,
        "driver_type": agent.driver.value if agent.driver else None,
        "prompt": "",
        "error_message": current_error_message,
    }


class AgentListHandler(BaseHandler):
    """GET /agents/list.json?team_id=<id> - 获取 team 的成员配置列表"""

    async def get(self):
        team_id_raw = self.get_query_argument("team_id", None)
        include_special_raw = self.get_query_argument("include_special", "false")
        if not team_id_raw:
            self.return_json({"agents": []})
            return

        team_id = int(team_id_raw)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        agents = await gtAgentManager.get_team_agents(team.id)
        runtime_status_map = agentService.get_team_runtime_status_map(team.id)
        include_special = include_special_raw.strip().lower() in {"1", "true", "yes", "on"}

        items = []
        for agent in agents:
            items.append({
                "id": agent.id,
                "name": agent.name,
                "employee_number": agent.employee_number,
                "role_template_id": agent.role_template_id,
                "team_id": agent.team_id,
                "status": runtime_status_map.get(agent.id, AgentStatus.IDLE).name,
                "employ_status": agent.employ_status.name if agent.employ_status else None,
                "model": agent.model,
                "driver": agent.driver.value if agent.driver else None,
                "special": None,
            })

        if include_special:
            items.extend([
                {
                    "id": int(SpecialAgent.OPERATOR.value),
                    "name": SpecialAgent.OPERATOR.name,
                    "employee_number": None,
                    "role_template_id": None,
                    "team_id": None,
                    "status": AgentStatus.IDLE.name,
                    "employ_status": None,
                    "model": "",
                    "driver": None,
                    "special": "operator",
                },
                {
                    "id": int(SpecialAgent.SYSTEM.value),
                    "name": SpecialAgent.SYSTEM.name,
                    "employee_number": None,
                    "role_template_id": None,
                    "team_id": None,
                    "status": AgentStatus.IDLE.name,
                    "employ_status": None,
                    "model": "",
                    "driver": None,
                    "special": "system",
                },
            ])

        self.return_json({"agents": items})


class TeamAgentsSaveHandler(BaseHandler):
    """PUT /teams/<id>/agents/save.json - 全量覆盖 Agent 列表"""

    async def put(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(AgentsSaveRequest)

        request_ids = [a.id for a in request.agents if a.id is not None]
        existing_agents = await gtAgentManager.get_team_agents(team_id)
        existing_ids = {a.id for a in existing_agents}

        invalid_ids = [id_ for id_ in request_ids if id_ not in existing_ids]
        assertUtil.assertEqual(
            len(invalid_ids), 0,
            error_message=f"Agent ID 不存在于当前 team: {invalid_ids}",
            error_code="agent_not_found",
        )

        final_names = [m.name for m in request.agents]
        duplicate_names = [n for n in final_names if final_names.count(n) > 1]
        assertUtil.assertEqual(
            len(duplicate_names), 0,
            error_message=f"agent name 重复: {duplicate_names}",
            error_code="duplicate_agent_name",
        )

        await _assert_role_templates_exist([a.role_template_id for a in request.agents])
        updated_agents = await agentService.overwrite_team_agents(
            team_id,
            [
                GtAgent(
                    id=item.id,
                    team_id=team_id,
                    name=item.name,
                    role_template_id=item.role_template_id,
                    model=item.model,
                    driver=item.driver,
                )
                for item in request.agents
            ],
        )

        await teamService.hot_reload_team(team.name)

        self.return_json({
            "status": "ok",
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "employee_number": agent.employee_number,
                    "role_template_id": agent.role_template_id,
                    "model": agent.model,
                    "driver": agent.driver.value,
                }
                for agent in updated_agents
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
        await _assert_role_templates_exist([item.role_template_id for item in request.agents])

        existing_by_id = {agent.id: agent for agent in existing_agents}
        for item in request.agents:
            agent = existing_by_id[item.id]
            agent.name = item.name
            agent.role_template_id = item.role_template_id
            agent.model = item.model
            agent.driver = item.driver
            await agent.aio_save()

        await teamService.hot_reload_team(team.name)
        self.return_success()


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

        self.return_json(await _build_agent_detail_payload(agent))


class AgentDetailByIdHandler(BaseHandler):
    """GET /agents/<id>.json - 获取单个成员配置详情"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agents = await gtAgentManager.get_agents_by_ids([agent_id])
        agent = agents[0] if agents else None
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent ID '{agent_id}' not found",
            error_code="agent_not_found",
        )
        self.return_json(await _build_agent_detail_payload(agent))


class AgentResumeHandler(BaseHandler):
    """POST /agents/<agent_id>/resume.json - 对 FAILED 状态的 Agent 触发续跑"""

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agent = None
        try:
            agent = agentService.get_agent(agent_id)
        except KeyError:
            pass
        assertUtil.assertNotNull(agent, None, f"运行时 Agent ID '{agent_id}' 不存在", "agent_not_found")
        assertUtil.assertTrue(agent.status == AgentStatus.FAILED, None, f"Agent ID={agent.gt_agent.id} 当前状态不是 FAILED（当前: {agent.status.name}）", "agent_not_failed")

        await agent.resume_failed()

        self.return_json({"status": "resumed", "agent_id": agent.gt_agent.id})
