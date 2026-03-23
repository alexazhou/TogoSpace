import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from constants import OpenaiLLMApiRole
from tests.base import ServiceTestCase
from service import (
    roomService,
    agentService,
    funcToolService,
    messageBus,
    schedulerService as scheduler,
    ormService,
    persistenceService,
)

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test_chat_flow", "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestPersistenceRestoreIntegration(ServiceTestCase):
    async def _reset_runtime_services(self):
        scheduler.shutdown()
        funcToolService.shutdown()
        messageBus.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()
        roomService.shutdown()

    def setup_method(self):
        self._run_maybe_async(self._reset_runtime_services())

    def teardown_method(self):
        self._run_maybe_async(self._reset_runtime_services())

    async def _bootstrap(self, db_path: Path):
        agents_config = json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())
        team_config = json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read())

        await roomService.startup()
        await funcToolService.startup()
        await agentService.startup()
        await ormService.startup(str(db_path))
        await persistenceService.startup()

        agentService.load_agent_config(agents_config)
        await agentService.create_team_agents([team_config])
        await roomService.create_rooms([team_config])
        await persistenceService.restore_runtime_state(agentService.get_all_agents(), roomService.get_all_rooms())
        await scheduler.startup([team_config])
        return team_config

    async def test_room_requires_explicit_start_before_scheduler_runs(self, tmp_path: Path):
        await self._bootstrap(tmp_path / "state.db")

        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert len(room.messages) == 1

        async def fake_infer(model, ctx):
            return self.normalize_to_mock({"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "hello"}}]})

        with self.patch_infer(handler=fake_infer):
            run_task = asyncio.create_task(scheduler.run())
            await asyncio.sleep(0.3)
            agent_messages = [m for m in room.messages if m.sender_name != "system"]
            assert len(agent_messages) == 0

            room.activate_scheduling()
            await asyncio.sleep(0.8)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        agent_messages = [m for m in room.messages if m.sender_name != "system"]
        assert len(agent_messages) >= 1

    async def test_restore_runtime_state_recovers_room_and_agent_history(self, tmp_path: Path):
        db_path = tmp_path / "state.db"

        await self._bootstrap(db_path)

        room = roomService.get_room_by_key(f"general@{TEAM}")

        replies = {
            "alice": [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "from alice"}, "id": "a1"}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}, "id": "a2"}]}],
            "bob": [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "from bob"}, "id": "b1"}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}, "id": "b2"}]}],
        }

        async def fake_infer(model, ctx):
            name = next((n for n in replies if n in ctx.system_prompt), None)
            res = replies[name].pop(0) if name and replies[name] else {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "..."}}]}
            return self.normalize_to_mock(res)

        with self.patch_infer(handler=fake_infer):
            run_task = asyncio.create_task(scheduler.run())
            room.activate_scheduling()
            await asyncio.sleep(1.0)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        assert any(m.content == "from alice" for m in room.messages)
        assert any(m.content == "from bob" for m in room.messages)
        assert agentService.get_agent(TEAM, "alice")._history

        # 手动清理服务以模拟重启
        scheduler.shutdown()
        funcToolService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()
        roomService.shutdown()

        # 重启并恢复状态
        await self._bootstrap(db_path)

        restored_room = roomService.get_room_by_key(f"general@{TEAM}")
        restored_alice = agentService.get_agent(TEAM, "alice")

        assert any(m.content == "from alice" for m in restored_room.messages)
        assert any(m.content == "from bob" for m in restored_room.messages)
        assert any(msg.content and "alice" in msg.content for msg in restored_alice._history if msg.content)
