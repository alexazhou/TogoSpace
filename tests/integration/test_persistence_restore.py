import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from constants import OpenaiLLMApiRole
from tests.base import ServiceTestCase
from util.llm_api_util import LlmApiMessage, ToolCall
from service import (
    room_service,
    agent_service,
    func_tool_service,
    scheduler_service as scheduler,
    orm_service,
    persistence_service,
)

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_multi_agent", "config")


def _make_infer_response(content=None, tool_calls=None):
    msg = LlmApiMessage(role=OpenaiLLMApiRole.ASSISTANT, content=content, tool_calls=tool_calls)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _send_msg_tool_call(room_name: str, msg: str, call_id="c1") -> ToolCall:
    return ToolCall(
        id=call_id,
        function={"name": "send_chat_msg", "arguments": json.dumps({"room_name": room_name, "msg": msg})},
    )


class TestPersistenceRestoreIntegration(ServiceTestCase):
    async def _bootstrap(self, db_path: Path):
        agents_config = json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())
        team_config = json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read())

        await room_service.startup()
        await func_tool_service.startup()
        await agent_service.startup()
        await orm_service.startup(str(db_path))
        await persistence_service.startup(enabled=True)

        agent_service.load_agent_config(agents_config)
        await agent_service.create_team_agents([team_config])
        await room_service.create_room(TEAM, "general", ["alice", "bob"], max_turns=2, emit_initial_message=False)
        await persistence_service.restore_runtime_state(agent_service.get_all_agents(), room_service.get_all_rooms())
        await scheduler.startup([team_config])
        return team_config

    async def test_room_requires_explicit_start_before_scheduler_runs(self, tmp_path: Path):
        await self.areset_services()
        await self._bootstrap(tmp_path / "state.db")

        room = room_service.get_room(f"general@{TEAM}")
        assert len(room.messages) == 1

        async def fake_infer(model, ctx):
            return _make_infer_response(tool_calls=[_send_msg_tool_call("general", "hello")])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            run_task = asyncio.create_task(scheduler.run())
            await asyncio.sleep(0.3)
            agent_messages = [m for m in room.messages if m.sender_name != "system"]
            assert len(agent_messages) == 0

            room.start_scheduling()
            await asyncio.sleep(0.8)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        agent_messages = [m for m in room.messages if m.sender_name != "system"]
        assert len(agent_messages) >= 1

    async def test_restore_runtime_state_recovers_room_and_agent_history(self, tmp_path: Path):
        db_path = tmp_path / "state.db"

        await self.areset_services()
        await self._bootstrap(db_path)

        room = room_service.get_room(f"general@{TEAM}")

        replies = {
            "alice": [_make_infer_response(tool_calls=[_send_msg_tool_call("general", "from alice", "a1")])],
            "bob": [_make_infer_response(tool_calls=[_send_msg_tool_call("general", "from bob", "b1")])],
        }

        async def fake_infer(model, ctx):
            name = next((n for n in replies if n in ctx.system_prompt), None)
            if name and replies[name]:
                return replies[name].pop(0)
            return _make_infer_response(tool_calls=[_send_msg_tool_call("general", "...")])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            run_task = asyncio.create_task(scheduler.run())
            room.start_scheduling()
            await asyncio.sleep(1.0)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        assert any(m.content == "from alice" for m in room.messages)
        assert any(m.content == "from bob" for m in room.messages)
        assert agent_service.get_agent(TEAM, "alice")._history

        await self.acleanup_services()

        await self.areset_services()
        await self._bootstrap(db_path)

        restored_room = room_service.get_room(f"general@{TEAM}")
        restored_alice = agent_service.get_agent(TEAM, "alice")

        assert any(m.content == "from alice" for m in restored_room.messages)
        assert any(m.content == "from bob" for m in restored_room.messages)
        assert any(msg.content and "alice" in msg.content for msg in restored_alice._history if msg.content)
