import os
import sys
from pathlib import Path

import pytest

from service import orm_service, persistence_service, room_service
from service.agent_service import Agent
from util.llm_api_util import LlmApiMessage, OpenaiLLMApiRole
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
class TestPersistenceService(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await room_service.startup()

    def setup_method(self):
        # 清理 room_service 的全局状态，避免测试间污染
        room_service.shutdown()
        # 关闭可能残留的 orm_service 和 persistence_service 连接
        try:
            orm_service._session = None
            persistence_service._enabled = False
        except Exception:
            pass

    async def test_restore_runtime_state_restores_room_history_and_read_index(self, tmp_path: Path):
        db_path = tmp_path / "runtime_test_room.db"

        async def _persist():
            await orm_service.startup(str(db_path))
            await persistence_service.startup(enabled=True)

        await _persist()

        await room_service.startup()
        await room_service.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3)
        room = room_service.get_room(f"r1@{TEAM}")
        await room.add_message("alice", "hello")
        await room.get_unread_messages("bob")
        await room.add_message("bob", "world")
        await room.get_unread_messages("alice")

        # 手动关闭，模拟进程重启
        await persistence_service.shutdown()
        await orm_service.shutdown()
        room_service.shutdown()

        # 重启并恢复
        await orm_service.startup(str(db_path))
        await persistence_service.startup(enabled=True)
        await room_service.startup()
        await room_service.create_rooms(TEAMS_CONFIG)
        restored = room_service.get_room(f"r1@{TEAM}")

        await persistence_service.restore_runtime_state([], [restored])

        assert [m.content for m in restored.messages] == [
            "r1 房间已经创建，当前房间成员：alice、bob",
            "hello",
            "world",
        ]
        assert restored.export_agent_read_index()["alice"] == 3
        assert restored.export_agent_read_index()["bob"] == 2

        # 清理
        await persistence_service.shutdown()
        await orm_service.shutdown()
        room_service.shutdown()

    async def test_restore_runtime_state_restores_agent_history(self, tmp_path: Path):
        db_path = tmp_path / "runtime_test_agent.db"

        async def _persist():
            await orm_service.startup(str(db_path))
            await persistence_service.startup(enabled=True)

        await _persist()

        agent = Agent("alice", TEAM, "sys", "test-model")
        agent._history = [
            LlmApiMessage.text(OpenaiLLMApiRole.USER, "u1"),
            LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1"),
        ]
        await persistence_service.append_agent_history_messages(agent.key, agent.dump_history_messages())

        # 手动关闭，模拟进程重启
        await persistence_service.shutdown()
        await orm_service.shutdown()

        # 重启并恢复
        await orm_service.startup(str(db_path))
        await persistence_service.startup(enabled=True)

        fresh_agent = Agent("alice", TEAM, "sys", "test-model")
        await persistence_service.restore_runtime_state([fresh_agent], [])

        assert [m.content for m in fresh_agent._history] == ["u1", "a1"]

        # 清理
        await persistence_service.shutdown()
        await orm_service.shutdown()
