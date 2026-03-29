import os
import sys
from pathlib import Path

import pytest

from dal.db import gtTeamManager, gtAgentManager
from service import roleTemplateService, agentService, ormService, persistenceService, roomService, messageBus
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
        await roleTemplateService.startup()
        team = await gtTeamManager.upsert_team(TeamConfig(name=TEAM))
        await gtAgentManager.batch_save_agents(team.id, [
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob"),
        ])
        await roomService.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3)
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
        await roomService.create_rooms(TEAMS_CONFIG)
        cls.restored = roomService.get_room_by_key(f"r1@{TEAM}")
        await roomService.restore_state()

    @classmethod
    async def async_teardown_class(cls):
        messageBus.shutdown()
        await roleTemplateService.shutdown()
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

        agent = Agent("alice", TEAM, "sys", "test-model")
        agent._history = [
            OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1"),
            OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1"),
        ]
        for item in agent.dump_history_messages():
            await persistenceService.append_agent_history_message(item)

        # 模拟进程重启
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()

        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        configUtil.load(os.path.join(os.path.dirname(__file__), "../../config"), force_reload=True)
        await roleTemplateService.startup()
        await agentService.startup()
        await agentService.create_team_agents([
            TeamConfig(
                name=TEAM,
                members=[AgentConfig(name="alice", role_template="alice")],
                preset_rooms=[],
            )
        ])
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
