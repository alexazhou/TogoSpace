"""活动记录查询接口。"""
import logging

from controller.baseController import BaseHandler
from dal.db import gtAgentActivityManager

logger = logging.getLogger(__name__)


class AgentActivitiesHandler(BaseHandler):
    """GET /agents/{agent_id}/activities.json"""

    async def get(self, agent_id: str) -> None:
        activities = await gtAgentActivityManager.list_agent_activities(int(agent_id))
        self.return_json({"activities": activities})


class TeamActivitiesHandler(BaseHandler):
    """GET /teams/{team_id}/activities.json"""

    async def get(self, team_id: str) -> None:
        activities = await gtAgentActivityManager.list_team_activities(int(team_id))
        self.return_json({"activities": activities})


class ActivitiesHandler(BaseHandler):
    """GET /activities.json?room_id={room_id}"""

    async def get(self) -> None:
        room_id_str = self.get_argument("room_id", default=None)
        room_id = int(room_id_str) if room_id_str else None
        activities = await gtAgentActivityManager.list_activities(room_id=room_id)
        self.return_json({"activities": activities})
