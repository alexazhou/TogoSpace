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
from util.llmApiUtil import LlmApiMessage, ToolCall
from constants import OpenaiLLMApiRole
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestIntegrationMultiAgentChat(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 按真实启动顺序拉起 service，并加载 integration 专用配置。
        agents_config = json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())
        team_config   = json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read())
        await roomService.startup()
        await roomService.create_room(TEAM, "general", ["alice", "bob"])
        await funcToolService.startup()
        await agentService.startup()
        agentService.load_agent_config(agents_config)
        await agentService.create_team_agents([team_config])
        await scheduler.startup([team_config])

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
            await roomService.create_room(TEAM, "general", ["alice", "bob"], max_turns=1)
            room = roomService.get_room_by_key(room_key)
            run_task = asyncio.create_task(scheduler.run())
            room.activate_scheduling()
            await asyncio.sleep(1)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        agent_messages = [m for m in room.messages if m.sender_name != "system"]
        assert len(agent_messages) >= 2

    async def test_tool_call_result_appended_to_history(self):
        """验证 tool_call 结果被正确追加到 agent history。"""
        room_key = f"general@{TEAM}"
        room = roomService.get_room_by_key(room_key)
        await room.add_message("system", "开始聊天")

        alice = agentService.get_agent(TEAM, "alice")
        resps = [
            {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "hello"}}]},
            {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            {"content": "done"},
        ]
        with self.patch_infer(responses=resps):
            await alice.run_chat_turn(room.room_id, max_function_calls=5)

        tool_results = [m for m in alice._history if m.role == OpenaiLLMApiRole.TOOL]
        assert len(tool_results) >= 1
        assert json.loads(tool_results[0].content)["success"]

    async def test_turn_checker_forces_send_chat_msg(self):
        """直接输出文字时 turn_checker 应注入 hint，迫使 agent 改用工具。"""
        room_key = f"general@{TEAM}"
        room = roomService.get_room_by_key(room_key)
        await room.add_message("system", "开始聊天")

        alice = agentService.get_agent(TEAM, "alice")
        resps = [
            {"content": "我直接回复"},
            {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "最终消息"}}]},
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
            run_task = asyncio.create_task(scheduler.run())
            await roomService.create_room(TEAM, "general", ["alice", "bob"], max_turns=2)
            room = roomService.get_room_by_key(room_key)
            room.activate_scheduling()
            for _ in range(20):
                if room.state.value == "idle":
                    break
                await asyncio.sleep(0.5)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=5.0)

        # 1 条公告 + 2轮×2人 = 5 条消息
        assert len(room.messages) == 5
