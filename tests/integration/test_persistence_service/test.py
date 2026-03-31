import os
import sys
from pathlib import Path

import pytest

from dal.db import gtTeamManager, gtAgentManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtTeam import GtTeam
from service import presetService, agentService, ormService, persistenceService, roomService, messageBus
from service.agentService import Agent
from util import configUtil
from util.llmApiUtil import OpenAIMessage, OpenaiLLMApiRole
from util.configTypes import TeamConfig, AgentConfig, TeamRoomConfig
from ...base import ServiceTestCase

TEAM = "test_team"
TEAMS_CONFIG = [TeamConfig(
    name=TEAM,
    members=[
        AgentConfig(name="alice", role_template="alice"),
        AgentConfig(name="bob", role_template="bob"),
    ],
    preset_rooms=[TeamRoomConfig(name="r1", members=["alice", "bob"], max_turns=3)],
)]

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestRestoreRoomHistory(ServiceTestCase):
    """重启后 restore_runtime_state 能恢复房间消息历史和已读游标。"""

    db_path: Path = None

    @classmethod
    async def async_setup_class(cls):
        cls.db_path = Path(cls._get_test_db_path())
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()
        await messageBus.startup()
        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await roomService.startup()
        await presetService.import_role_templates_from_app_config()
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        configs = [
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob"),
        ]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        await roomService.ensure_room_record(TEAM, "r1", ["alice", "bob"], max_turns=3)
        room = roomService.get_room_by_key(f"r1@{TEAM}")
        await room.activate_scheduling()
        await room.add_message("alice", "hello")
        await room.get_unread_messages("bob")
        await room.add_message("bob", "world")
        await room.get_unread_messages("alice")

        # 模拟进程重启：关闭再重新打开同一 DB
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()

        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await roomService.startup()
        await roomService.load_rooms_from_db()
        cls.restored = roomService.get_room_by_key(f"r1@{TEAM}")
        await roomService.restore_state()

    @classmethod
    async def async_teardown_class(cls):
        messageBus.shutdown()
        await presetService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()

    async def test_messages_restored(self):
        assert [m.content for m in self.restored.messages] == [
            "r1 房间已经创建，当前房间成员：alice、bob",
            "hello",
            "world",
        ]

    async def test_read_index_restored(self):
        assert self.restored.export_member_read_index()["alice"] == 3
        assert self.restored.export_member_read_index()["bob"] == 2



class TestRestoreAgentHistory(ServiceTestCase):
    """重启后 restore_runtime_state 能恢复 Agent 对话历史。"""

    db_path: Path = None

    @classmethod
    async def async_setup_class(cls):
        cls.db_path = Path(cls._get_test_db_path())
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()
        roomService.shutdown()
        await messageBus.startup()
        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await agentService.startup()
        await presetService.import_role_templates_from_app_config()
        configUtil.load(os.path.join(os.path.dirname(__file__), "../../config"), force_reload=True)
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        agents = await ServiceTestCase.convert_to_gt_agents(
            team.id,
            [AgentConfig(name="alice", role_template="alice")],
        )
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice_row = await gtAgentManager.get_agent(team.id, "alice")
        assert alice_row is not None
        await persistenceService.append_agent_history_message(
            GtAgentHistory(
                agent_id=alice_row.id,
                seq=0,
                message_json=OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1").model_dump_json(exclude_none=True),
            )
        )
        await persistenceService.append_agent_history_message(
            GtAgentHistory(
                agent_id=alice_row.id,
                seq=1,
                message_json=OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1").model_dump_json(exclude_none=True),
            )
        )

        # 模拟进程重启
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()

        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        configUtil.load(os.path.join(os.path.dirname(__file__), "../../config"), force_reload=True)
        await presetService.import_role_templates_from_app_config()
        await agentService.startup()
        await agentService.create_team_agents_from_db()
        cls.fresh_agent = agentService.get_team_agent(TEAM, "alice")
        await agentService.restore_state()

    @classmethod
    async def async_teardown_class(cls):
        messageBus.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_history_restored(self):
        assert [m.content for m in self.fresh_agent._history] == ["u1", "a1"]
