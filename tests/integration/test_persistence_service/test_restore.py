import asyncio
import json
import os
import sys

import pytest

from constants import OpenaiLLMApiRole, SpecialAgent
from dal.db import gtTeamManager
from tests.base import ServiceTestCase
from util.configTypes import RoleTemplate, TeamConfig
from service import (
    roomService,
    roleTemplateService,
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
        self.cleanup_sqlite_files()
        self._run_maybe_async(self._reset_runtime_services())

    def teardown_method(self):
        self._run_maybe_async(self._reset_runtime_services())
        self.cleanup_sqlite_files()

    async def _bootstrap(self):
        agents_config = [RoleTemplate.model_validate(a) for a in json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())]
        team_config = TeamConfig.model_validate(json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read()))

        from src.db import migrate_database
        migrate_database(self.TEST_DB_PATH)

        await roomService.startup()
        await funcToolService.startup()
        await roleTemplateService.startup()
        await ormService.startup(self.TEST_DB_PATH)
        await persistenceService.startup()

        roleTemplateService.load_role_template_config(agents_config)
        await agentService.startup()
        await gtTeamManager.import_team_from_config(team_config)
        await agentService.load_team_ids([team_config])
        await agentService.create_team_agents([team_config])
        await roomService.create_rooms([team_config])
        await agentService.restore_state()
        await roomService.restore_state()
        await scheduler.startup([team_config])
        return team_config

    async def test_room_requires_explicit_start_before_scheduler_runs(self):
        await self._bootstrap()

        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert len(room.messages) == 1

        async def fake_infer(model, ctx):
            return self.normalize_to_mock({"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "hello"}}]})

        with self.patch_infer(handler=fake_infer):
            run_task = asyncio.create_task(scheduler.run())
            await asyncio.sleep(0)
            agent_messages = [m for m in room.messages if SpecialAgent.value_of(m.sender_name) != SpecialAgent.SYSTEM]
            assert len(agent_messages) == 0

            room.activate_scheduling()
            await self.wait_until(
                lambda: len([m for m in room.messages if SpecialAgent.value_of(m.sender_name) != SpecialAgent.SYSTEM]) >= 1,
                timeout=2.0,
                message="房间激活后未在限时内收到 Agent 回复",
            )
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        agent_messages = [m for m in room.messages if SpecialAgent.value_of(m.sender_name) != SpecialAgent.SYSTEM]
        assert len(agent_messages) >= 1

    async def test_restore_runtime_state_recovers_room_and_agent_history(self):
        await self._bootstrap()

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
            await self.wait_until(
                lambda: any(m.content == "from alice" for m in room.messages)
                and any(m.content == "from bob" for m in room.messages),
                timeout=2.0,
                message="恢复前的对话未在限时内完成",
            )
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        assert any(m.content == "from alice" for m in room.messages)
        assert any(m.content == "from bob" for m in room.messages)
        assert agentService.get_team_agent(TEAM, "alice")._history

        # 手动清理服务以模拟重启
        scheduler.shutdown()
        funcToolService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()
        roomService.shutdown()

        # 重启并恢复状态
        await self._bootstrap()

        restored_room = roomService.get_room_by_key(f"general@{TEAM}")
        restored_alice = agentService.get_team_agent(TEAM, "alice")

        assert any(m.content == "from alice" for m in restored_room.messages)
        assert any(m.content == "from bob" for m in restored_room.messages)
        assert any(msg.content and "alice" in msg.content for msg in restored_alice._history if msg.content)
