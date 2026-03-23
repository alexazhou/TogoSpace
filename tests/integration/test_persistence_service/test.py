import os
import sys
from pathlib import Path

import pytest

from service import ormService, persistenceService, roomService
from service.agentService import Agent
from util.llmApiUtil import LlmApiMessage, OpenaiLLMApiRole
from ...base import ServiceTestCase

TEAM = "test_team"
TEAMS_CONFIG = [{
    "name": TEAM,
    "groups": [{
        "name": "r1",
        "type": "group",
        "members": ["alice", "bob"],
        "max_turns": 3,
    }],
}]

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestpersistenceService(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await roomService.startup()

    def setup_method(self):
        # 清理 roomService 的全局状态，避免测试间污染
        roomService.shutdown()
        # 关闭可能残留的 ormService 和 persistenceService 连接
        try:
            ormService._session = None
            persistenceService._enabled = False
        except Exception:
            pass

    async def test_restore_runtime_state_restores_room_history_and_read_index(self, tmp_path: Path):
        db_path = tmp_path / "runtime_test_room.db"

        async def _persist():
            await ormService.startup(str(db_path))
            await persistenceService.startup(enabled=True)

        await _persist()

        await roomService.startup()
        await roomService.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3)
        room = roomService.get_room_by_key(f"r1@{TEAM}")
        await room.add_message("alice", "hello")
        await room.get_unread_messages("bob")
        await room.add_message("bob", "world")
        await room.get_unread_messages("alice")

        # 手动关闭，模拟进程重启
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()

        # 重启并恢复
        await ormService.startup(str(db_path))
        await persistenceService.startup(enabled=True)
        await roomService.startup()
        await roomService.create_rooms(TEAMS_CONFIG)
        restored = roomService.get_room_by_key(f"r1@{TEAM}")

        await persistenceService.restore_runtime_state([], [restored])

        assert [m.content for m in restored.messages] == [
            "r1 房间已经创建，当前房间成员：alice、bob",
            "hello",
            "world",
        ]
        assert restored.export_agent_read_index()["alice"] == 3
        assert restored.export_agent_read_index()["bob"] == 2

        # 清理
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()

    async def test_restore_runtime_state_restores_agent_history(self, tmp_path: Path):
        db_path = tmp_path / "runtime_test_agent.db"

        async def _persist():
            await ormService.startup(str(db_path))
            await persistenceService.startup(enabled=True)

        await _persist()

        agent = Agent("alice", TEAM, "sys", "test-model")
        agent._history = [
            LlmApiMessage.text(OpenaiLLMApiRole.USER, "u1"),
            LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1"),
        ]
        await persistenceService.append_agent_history_messages(agent.key, agent.dump_history_messages())

        # 手动关闭，模拟进程重启
        await persistenceService.shutdown()
        await ormService.shutdown()

        # 重启并恢复
        await ormService.startup(str(db_path))
        await persistenceService.startup(enabled=True)

        fresh_agent = Agent("alice", TEAM, "sys", "test-model")
        await persistenceService.restore_runtime_state([fresh_agent], [])

        assert [m.content for m in fresh_agent._history] == ["u1", "a1"]

        # 清理
        await persistenceService.shutdown()
        await ormService.shutdown()
