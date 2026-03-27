from typing import List

import service.agentService as agentService
import service.roleTemplateService as roleTemplateService
from service.agentService import Agent
from model.coreModel.gtCoreWebModel import GtCoreAgentInfo
from controller.baseController import BaseHandler
from constants import MemberStatus
from dal.db import gtTeamManager
from util import assertUtil, configUtil


class AgentListHandler(BaseHandler):
    async def get(self):
        team_id_raw = self.get_query_argument("team_id", None)
        team_name = self.get_query_argument("team_name", None)
        if team_id_raw:
            team = await gtTeamManager.get_team_by_id(int(team_id_raw))
            assertUtil.assertNotNull(
                team,
                error_message=f"Team ID '{team_id_raw}' not found",
                error_code="team_not_found",
            )
            if team is None:
                return
            team_name = team.name
        if team_name:
            agents: List[Agent] = agentService.get_all_agents()
            agents = [a for a in agents if a.team_name == team_name]
            data = [a.get_info().model_dump(mode="json") for a in agents]
        else:
            data = [
                GtCoreAgentInfo(
                    name=definition.name,
                    template_name=definition.name,
                    model=definition.model or "",
                    team_name="",
                    status=MemberStatus.IDLE,
                ).model_dump(mode="json")
                for definition in roleTemplateService.get_all_role_templates()
            ]
        self.return_json({"agents": data})


class AgentDetailHandler(BaseHandler):
    async def get(self, team_id_str: str, agent_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        if team is None:
            return

        agent = agentService.find_team_agent(team.name, agent_name)
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent '{agent_name}' not found in team '{team.name}'",
            error_code="agent_not_found",
        )
        if agent is None:
            return

        definition = roleTemplateService.get_role_template(agent.template_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{agent.template_name}' not found",
            error_code="role_template_not_found",
        )
        if definition is None:
            return

        if definition.system_prompt:
            prompt = definition.system_prompt
        else:
            prompt = configUtil.load_prompt(definition.prompt_file)

        self.return_json(
            {
                **agent.get_info().model_dump(mode="json"),
                "role_template_name": agent.template_name,
                "driver_type": agent.driver.driver_type,
                "prompt": prompt,
            }
        )
