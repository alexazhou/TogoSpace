"""活动记录查询接口。"""
import logging

from constants import AgentActivityType
from controller.baseController import BaseHandler
from dal.db import gtAgentActivityManager

logger = logging.getLogger(__name__)


class AgentActivitiesHandler(BaseHandler):
    """GET /agents/{agent_id}/activities.json?exclude=AGENT_STATE&limit=50&before_id=123"""

    async def get(self, agent_id: str) -> None:
        exclude_raw = self.get_arguments("exclude")
        exclude_types = [AgentActivityType[name.upper()] for name in exclude_raw]
        limit_raw = self.get_query_argument("limit", "100")
        before_id_raw = self.get_query_argument("before_id", None)
        limit = max(1, min(int(limit_raw), 100))
        before_id = int(before_id_raw) if before_id_raw is not None else None
        activities, has_more = await gtAgentActivityManager.list_agent_activities_page(
            int(agent_id),
            limit=limit,
            before_id=before_id,
            exclude_types=exclude_types or None,
        )
        self.return_json({
            "activities": activities,
            "pagination": {
                "has_more": has_more,
                "before_id": before_id,
                "limit": limit,
            },
        })


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
