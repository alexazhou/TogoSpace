import os
import sys

import service.agentService as agentService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from dal.db import gtAgentManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestRoomApiPayload(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_to_dict_includes_current_turn_agent_id(self):
        await self.create_room(TEAM, "r", ["alice", "bob"], max_turns=5)
        room = roomService.get_room_by_key(f"r@{TEAM}")

        assert await room.activate_scheduling()

        payload = room.to_dict()
        current_turn_agent_id = payload["current_turn_agent_id"]

        assert current_turn_agent_id is not None
        assert current_turn_agent_id > 0
