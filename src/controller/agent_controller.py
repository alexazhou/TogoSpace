from typing import List
import service.agentService as agentService
from service.agentService import Agent
from model.web_model import AgentInfo
from controller.base_controller import BaseHandler
from constants import AgentStatus


class AgentListHandler(BaseHandler):
    async def get(self):
        agents: List[Agent] = agentService.get_all_agents()
        data = [
            AgentInfo(
                name=a.name,
                model=a.model,
                team_name=a.team_name,
                status=AgentStatus.ACTIVE if a.is_active else AgentStatus.IDLE,
            ).model_dump(mode="json")
            for a in agents
        ]
        self.return_json({"agents": data})
