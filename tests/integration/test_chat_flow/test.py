"""integration tests — 验证多 Agent 完整对话流程（mock LLM，真实 service 层）"""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.roomService as roomService
import service.agentService as agentService
import service.funcToolService as funcToolService
import service.schedulerService as scheduler
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.presetService as presetService
from model.dbModel.gtAgentHistory import GtAgentHistory
from util import configUtil
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall
from constants import AgentHistoryTag, OpenaiLLMApiRole, RoomState
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestIntegrationMultiAgentChat(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 按真实启动顺序拉起 service，并加载 integration 专用配置。
        cfg = configUtil.load(_CONFIG_DIR, force_reload=True)
        team_config = cfg.teams[0]
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        await presetService._import_team_from_config(team_config)
        await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"])
        await funcToolService.startup()
        await agentService.startup()
        await agentService.create_team_agents_from_db()
        await scheduler.startup()

    @classmethod
    async def async_teardown_class(cls):
        scheduler.shutdown()
        await agentService.shutdown()
        funcToolService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_two_agents_exchange_messages(self):
        """alice 和 bob 各发一轮消息，general 房间应有消息。"""
        room_key = f"general@{TEAM}"

        call_seq = {
            "alice": [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "你好，bob！"}}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}],
            "bob":   [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "你好，alice！"}}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}],
        }

        async def fake_infer(model, ctx):
            name = next((n for n in call_seq if n in ctx.system_prompt), None)
            res = call_seq[name].pop(0) if name and call_seq[name] else {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "..."}}]}
            return self.normalize_to_mock(res)

        with self.patch_infer(handler=fake_infer):
            # 重新创建 max_turns=1 的同名房间，快速触发“每人一轮”场景。
            await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"], max_turns=1)
            room = roomService.get_room_by_key(room_key)
            await room.activate_scheduling()
            await self.wait_until(
                lambda: len([m for m in room.messages if m.sender_name != "system"]) >= 2,
                timeout=2.0,
                message="alice 和 bob 未在限时内完成一轮对话",
            )

        agent_messages = [m for m in room.messages if m.sender_name != "system"]
        assert len(agent_messages) >= 2

    async def test_tool_call_result_appended_to_history(self):
        """验证 tool_call 结果被正确追加到 agent history。"""
        await roomService.ensure_room_record(TEAM, "manual_turn", ["alice", "bob"])
        room = roomService.get_room_by_key(f"manual_turn@{TEAM}")
        await room.activate_scheduling()

        alice = agentService.get_team_agent(TEAM, "alice")
        alice.inject_history_messages([
            GtAgentHistory.from_openai_message(
                alice.agent_id,
                0,
                OpenAIMessage.text(OpenaiLLMApiRole.SYSTEM, "reset test turn state"),
            )
        ])
        call_seq = {
            "alice": [
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "manual_turn", "msg": "hello"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            ],
            "bob": [],
        }

        async def fake_infer(model, ctx):
            name = "alice" if "alice" in ctx.system_prompt else "bob"
            if call_seq[name]:
                return self.normalize_to_mock(call_seq[name].pop(0))
            # 兜底返回 finish，避免并发调度时 side_effect 耗尽导致 StopIteration。
            return self.normalize_to_mock({"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]})

        with self.patch_infer(handler=fake_infer):
            await alice.run_chat_turn(room.room_id, max_function_calls=5)

        tool_results = [m for m in alice._history if m.role == OpenaiLLMApiRole.TOOL]
        assert len(tool_results) >= 1
        assert json.loads(tool_results[0].content)["success"]
        assert any(AgentHistoryTag.ROOM_TURN_FINISH in msg.tags for msg in tool_results)

    async def test_turn_checker_forces_send_chat_msg(self):
        """直接输出文字时 turn_checker 应注入 hint，迫使 agent 改用工具。"""
        await roomService.ensure_room_record(TEAM, "turn_checker_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"turn_checker_room@{TEAM}")

        alice = agentService.get_team_agent(TEAM, "alice")
        alice.inject_history_messages([
            GtAgentHistory.from_openai_message(
                alice.agent_id,
                0,
                OpenAIMessage.text(OpenaiLLMApiRole.SYSTEM, "reset turn checker history"),
            )
        ])
        resps = [
            {"content": "我直接回复"},
            {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "turn_checker_room", "msg": "最终消息"}}]},
            {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
        ]
        with self.patch_infer(responses=resps):
            await alice.run_chat_turn(room.room_id, max_function_calls=5)

        assert any(m.content == "最终消息" for m in room.messages)

    async def test_scheduler_terminates_after_max_turns(self):
        """max_turns 用尽后，通过观察 Room 状态并停止调度器。"""
        room_key = f"general@{TEAM}"
        room = roomService.get_room_by_key(room_key)

        # 预定义每个 agent 的调用序列
        call_seq = {
            "alice": [
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            ],
            "bob": [
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            ],
        }

        async def fake_infer(model, ctx):
            name = "alice" if "alice" in ctx.system_prompt else "bob"
            if call_seq[name]:
                res = call_seq[name].pop(0)
            else:
                res = {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}
            return self.normalize_to_mock(res)

        with self.patch_infer(handler=fake_infer):
            await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"], max_turns=2)
            room = roomService.get_room_by_key(room_key)
            await room.activate_scheduling()
            await self.wait_until(
                lambda: room.state == RoomState.IDLE,
                timeout=3.0,
                message="房间未在限时内进入 IDLE 状态",
            )

        # 1 条公告 + 2轮×2人 = 5 条消息
        assert len(room.messages) == 5
