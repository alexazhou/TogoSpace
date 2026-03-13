from typing import List
import service.agent_service as agent_service
from service.agent_service import Agent
from model.web_model import AgentInfo
from controller.base_controller import BaseHandler


class AgentListHandler(BaseHandler):
    async def get(self):
        agents: List[Agent] = agent_service.get_all_agents()
        data = [
            AgentInfo(
                name=a.name,
                model=a.model,
                team_name=a.team_name,
                status="active" if a.is_active else "idle",
            ).model_dump(mode="json")
            for a in agents
        ]
        self.return_json({"agents": data})
