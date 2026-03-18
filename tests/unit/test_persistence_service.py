from pathlib import Path

from service import orm_service, persistence_service, room_service
from service.agent_service import Agent
from util.llm_api_util import LlmApiMessage, OpenaiLLMApiRole
from ..base import ServiceTestCase

TEAM = "test_team"


class TestPersistenceService(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await cls.areset_services()
        await room_service.startup()

    def setup_method(self):
        room_service.shutdown()

    def test_restore_runtime_state_restores_room_history_and_read_index(self, tmp_path: Path):
        db_path = tmp_path / "runtime.db"

        async def _persist():
            await orm_service.startup(str(db_path))
            await persistence_service.startup(enabled=True)

        self._run_maybe_async(_persist())

        room_service.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3, emit_initial_message=False)
        room = room_service.get_room(f"r1@{TEAM}")
        room.add_message("system", room.build_initial_system_message())
        room.add_message("alice", "hello")
        room.get_unread_messages("bob")
        room.add_message("bob", "world")
        room.get_unread_messages("alice")

        room_service.shutdown()
        self._run_maybe_async(room_service.startup())
        room_service.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3, emit_initial_message=False)
        restored = room_service.get_room(f"r1@{TEAM}")

        persistence_service.restore_runtime_state([], [restored])

        assert [m.content for m in restored.messages] == [
            "r1 房间已经创建，当前房间成员：alice、bob",
            "hello",
            "world",
        ]
        assert restored.export_agent_read_index()["alice"] == 3
        assert restored.export_agent_read_index()["bob"] == 2

    def test_restore_runtime_state_restores_agent_history(self, tmp_path: Path):
        db_path = tmp_path / "runtime.db"

        async def _persist():
            await orm_service.startup(str(db_path))
            await persistence_service.startup(enabled=True)

        self._run_maybe_async(_persist())

        agent = Agent("alice", TEAM, "sys", "test-model")
        agent._history = [
            LlmApiMessage.text(OpenaiLLMApiRole.USER, "u1"),
            LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1"),
        ]
        persistence_service.append_agent_history_messages(agent.key, agent.dump_history_messages())

        fresh_agent = Agent("alice", TEAM, "sys", "test-model")
        persistence_service.restore_runtime_state([fresh_agent], [])

        assert [m.content for m in fresh_agent._history] == ["u1", "a1"]

    @classmethod
    async def async_teardown_class(cls):
        await cls.acleanup_services()
