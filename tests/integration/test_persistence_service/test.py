import os
import sys
from pathlib import Path

import pytest

from service import ormService, persistenceService, roomService, messageBus
from service.agentService import Agent
from util.llmApiUtil import LlmApiMessage, OpenaiLLMApiRole
from ...base import ServiceTestCase

TEAM = "test_team"
TEAMS_CONFIG = [{
    "name": TEAM,
    "preset_rooms": [{
        "name": "r1",
        "members": ["alice", "bob"],
        "max_turns": 3,
    }],
}]

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestRestoreRoomHistory(ServiceTestCase):
    """重启后 restore_runtime_state 能恢复房间消息历史和已读游标。"""

    db_path: Path = None

    @classmethod
    async def async_setup_class(cls):
        cls.db_path = Path(cls.get_test_db_path())
        cls.cleanup_sqlite_files(str(cls.db_path))
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()
        await messageBus.startup()
        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await roomService.startup()
        await roomService.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3)
        room = roomService.get_room_by_key(f"r1@{TEAM}")
        room.activate_scheduling()
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
        await persistenceService.restore_runtime_state([], [cls.restored])

    @classmethod
    async def async_teardown_class(cls):
        messageBus.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()
        if cls.db_path:
            cls.cleanup_sqlite_files(str(cls.db_path))

    async def test_messages_restored(self):
        assert [m.content for m in self.restored.messages] == [
            "hello",
            "world",
        ]

    async def test_read_index_restored(self):
        assert self.restored.export_agent_read_index()["alice"] == 3
        assert self.restored.export_agent_read_index()["bob"] == 2


@pytest.mark.forked
class TestRestoreAgentHistory(ServiceTestCase):
    """重启后 restore_runtime_state 能恢复 Agent 对话历史。"""

    db_path: Path = None

    @classmethod
    async def async_setup_class(cls):
        cls.db_path = Path(cls.get_test_db_path())
        cls.cleanup_sqlite_files(str(cls.db_path))
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()
        await messageBus.startup()
        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()

        agent = Agent("alice", TEAM, "sys", "test-model")
        agent._history = [
            LlmApiMessage.text(OpenaiLLMApiRole.USER, "u1"),
            LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1"),
        ]
        for item in agent.dump_history_messages():
            await persistenceService.append_agent_history_message(item)

        # 模拟进程重启
        await persistenceService.shutdown()
        await ormService.shutdown()

        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()

        cls.fresh_agent = Agent("alice", TEAM, "sys", "test-model")
        await persistenceService.restore_runtime_state([cls.fresh_agent], [])

    @classmethod
    async def async_teardown_class(cls):
        messageBus.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        if cls.db_path:
            cls.cleanup_sqlite_files(str(cls.db_path))

    async def test_history_restored(self):
        assert [m.content for m in self.fresh_agent._history] == ["u1", "a1"]
