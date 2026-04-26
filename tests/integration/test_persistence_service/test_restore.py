import os
import sys

import pytest

from constants import OpenaiApiRole, SpecialAgent, ScheduleState
from dal.db import gtAgentManager
from tests.base import ServiceTestCase
from util import configUtil
from service import (
    roomService,
    presetService,
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



class TestPersistenceRestoreIntegration(ServiceTestCase):
    async def _reset_runtime_services(self):
        scheduler.shutdown()
        funcToolService.shutdown()
        await messageBus.shutdown()
        await persistenceService.shutdown()
        # 先关闭 agentService，确保消费者任务停止后再关闭数据库连接
        await agentService.shutdown()
        roomService.shutdown()
        await ormService.shutdown()

    @pytest.fixture(autouse=True)
    async def _reset_between_tests(self):
        gtAgentManager.clear_agent_cache()  # 清空缓存，避免测试间数据污染
        self.cleanup_sqlite_files()
        await self._reset_runtime_services()
        yield
        await self._reset_runtime_services()
        self.cleanup_sqlite_files()

    async def _bootstrap(self):
        cfg = configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)
        team_config = cfg.teams[0]

        from src.db import migrate_database
        migrate_database(self._get_test_db_path())

        await roomService.startup()
        await funcToolService.startup()
        await ormService.startup(self._get_test_db_path())
        await persistenceService.startup()
        await presetService._import_role_templates_from_app_config()
        await agentService.startup()
        await presetService._import_team_from_config(team_config)
        await agentService.load_all_team_agents()
        await roomService.load_all_rooms()
        await agentService.restore_all_agents_runtime_state()
        await roomService.restore_all_rooms_runtime_state()
        await scheduler.startup()
        scheduler._schedule_state = ScheduleState.RUNNING
        return team_config

    async def test_room_requires_explicit_start_before_scheduler_runs(self):
        await self._bootstrap()

        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert len(room.messages) == 0

        async def fake_infer(model, ctx):
            return self.normalize_to_mock({"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "hello"}}]})

        with self.patch_infer(handler=fake_infer):
            agent_messages = [m for m in room.messages if m.sender_id != room.SYSTEM_MEMBER_ID]
            assert len(agent_messages) == 0

            await room.activate_scheduling()
            await self.wait_until(
                lambda: len([m for m in room.messages if m.sender_id != room.SYSTEM_MEMBER_ID]) >= 1,
                timeout=2.0,
                message="房间激活后未在限时内收到 Agent 回复",
            )

        agent_messages = [m for m in room.messages if m.sender_id != room.SYSTEM_MEMBER_ID]
        assert len(agent_messages) >= 1

    async def test_restore_runtime_state_recovers_room_and_agent_history(self):
        await self._bootstrap()

        room = roomService.get_room_by_key(f"general@{TEAM}")

        replies = {
            "alice": [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "from alice"}, "id": "a1"}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}, "id": "a2"}]}],
            "bob": [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "from bob"}, "id": "b1"}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}, "id": "b2"}]}],
        }

        async def fake_infer(model, ctx):
            name = next((n for n in replies if f"你当前的名字：{n}" in ctx.system_prompt), None)
            res = replies[name].pop(0) if name and replies[name] else {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "..."}}]}
            return self.normalize_to_mock(res)

        with self.patch_infer(handler=fake_infer):
            await room.activate_scheduling()
            await self.wait_until(
                lambda: any(m.content == "from alice" for m in room.messages)
                and any(m.content == "from bob" for m in room.messages),
                timeout=2.0,
                message="恢复前的对话未在限时内完成",
            )

        assert any(m.content == "from alice" for m in room.messages)
        assert any(m.content == "from bob" for m in room.messages)
        assert agentService.get_agent(agentService.get_agent_id_by_stable_name(room.team_id, "alice")).task_consumer._turn_runner._history

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
        restored_alice = agentService.get_agent(agentService.get_agent_id_by_stable_name(restored_room.team_id, "alice"))

        assert any(m.content == "from alice" for m in restored_room.messages)
        assert any(m.content == "from bob" for m in restored_room.messages)
        assert any(msg.content and "alice" in msg.content for msg in restored_alice.task_consumer._turn_runner._history if msg.content)
