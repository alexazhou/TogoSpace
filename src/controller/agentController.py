from typing import Any, List

import service.agentService as agentService
from service.agentService import Agent
from model.coreModel.gtCoreWebModel import AgentInfo
from controller.baseController import BaseHandler
from constants import AgentStatus
from dal.db import gtTeamManager
from util import assertUtil, configUtil


def _serialize_agent(agent: Agent) -> dict[str, Any]:
    return AgentInfo(
        name=agent.name,
        template_name=agent.template_name or None,
        model=agent.model,
        team_name=agent.team_name,
        status=AgentStatus.ACTIVE if agent.is_active else AgentStatus.IDLE,
    ).model_dump(mode="json")


class AgentListHandler(BaseHandler):
    async def get(self):
        team_name = self.get_query_argument("team_name", None)
        if team_name:
            agents: List[Agent] = agentService.get_all_agents()
            agents = [agent for agent in agents if agent.team_name == team_name]
            data = [_serialize_agent(agent) for agent in agents]
        else:
            data = [
                AgentInfo(
                    name=str(definition["name"]),
                    template_name=str(definition["name"]),
                    model=str(definition.get("model") or ""),
                    team_name="",
                    status=AgentStatus.IDLE,
                ).model_dump(mode="json")
                for definition in agentService.get_all_agent_definitions()
            ]
        self.return_json({"agents": data})


class AgentDetailHandler(BaseHandler):
    async def get(self, team_id_str: str, agent_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        if team is None:
            return

        agent = agentService.find_agent(team.name, agent_name)
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent '{agent_name}' not found in team '{team.name}'",
            error_code="agent_not_found",
        )
        if agent is None:
            return

        definition = agentService.get_agent_definition(agent.template_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Agent definition '{agent.template_name}' not found",
            error_code="agent_definition_not_found",
        )
        if definition is None:
            return

        if "system_prompt" in definition:
            prompt = definition["system_prompt"]
        else:
            prompt = configUtil.load_prompt(definition["prompt_file"])

        self.return_json(
            {
                **_serialize_agent(agent),
                "agent_name": agent.template_name,
                "driver_type": agent.driver.driver_type,
                "prompt": prompt,
            }
        )
