"""integration tests — 验证多 Agent 完整对话流程（mock LLM，真实 service 层）"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import service.room_service as room_service
import service.agent_service as agent_service
import service.func_tool_service as func_tool_service
import service.scheduler_service as scheduler
from util.llm_api_util import LlmApiMessage, ToolCall
from constants import OpenaiLLMApiRole
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


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


class TestIntegrationMultiAgentChat(ServiceTestCase):
    async def async_setup_method(self):
        agents_config = json.loads(open(os.path.join(_CONFIG_DIR, "agents.json")).read())
        team_config   = json.loads(open(os.path.join(_CONFIG_DIR, "team.json")).read())
        room_service.startup()
        room_service.create_room(TEAM, "general", ["alice", "bob"])
        func_tool_service.startup()
        agent_service.startup()
        agent_service.load_agent_config(agents_config)
        await agent_service.create_team_agents([team_config])
        scheduler.startup([team_config])

    async def test_two_agents_exchange_messages(self):
        """alice 和 bob 各发一轮消息，general 房间应有消息。"""
        room_key = f"general@{TEAM}"
        room = room_service.get_room(room_key)

        alice_reply = _send_msg_tool_call("general", "你好，bob！")
        bob_reply   = _send_msg_tool_call("general", "你好，alice！")
        call_seq = {
            "alice": [_make_infer_response(tool_calls=[alice_reply])],
            "bob":   [_make_infer_response(tool_calls=[bob_reply])],
        }

        async def fake_infer(model, ctx):
            name = next((n for n in call_seq if n in ctx.system_prompt), None)
            if name and call_seq[name]:
                return call_seq[name].pop(0)
            return _make_infer_response(tool_calls=[_send_msg_tool_call("general", "...")])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            room_service.create_room(TEAM, "general", ["alice", "bob"], max_turns=1)
            room = room_service.get_room(room_key)
            run_task = asyncio.create_task(scheduler.run())
            await asyncio.sleep(1)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

        agent_messages = [m for m in room.messages if m.sender_name != "system"]
        assert len(agent_messages) >= 2

    async def test_tool_call_result_appended_to_history(self):
        """验证 tool_call 结果被正确追加到 agent history。"""
        room_key = f"general@{TEAM}"
        room = room_service.get_room(room_key)
        room.add_message("system", "开始聊天")

        alice = agent_service.get_agent(TEAM, "alice")
        tc = _send_msg_tool_call("general", "hello")
        responses = [
            _make_infer_response(tool_calls=[tc]),
            _make_infer_response(content="done"),
        ]
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await alice.run_turn(room_key, max_function_calls=5)

        tool_results = [m for m in alice._history if m.role == OpenaiLLMApiRole.TOOL]
        assert len(tool_results) >= 1
        assert tool_results[0].content == "success"

    async def test_turn_checker_forces_send_chat_msg(self):
        """直接输出文字时 turn_checker 应注入 hint，迫使 agent 改用工具。"""
        room_key = f"general@{TEAM}"
        room = room_service.get_room(room_key)
        room.add_message("system", "开始聊天")

        alice = agent_service.get_agent(TEAM, "alice")
        tc = _send_msg_tool_call("general", "最终消息")
        responses = [
            _make_infer_response(content="我直接回复"),
            _make_infer_response(tool_calls=[tc]),
        ]
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await alice.run_turn(room_key, max_function_calls=5)

        assert any(m.content == "最终消息" for m in room.messages)

    async def test_scheduler_terminates_after_max_turns(self):
        """max_turns 用尽后，通过观察 Room 状态并停止调度器。"""
        room_key = f"general@{TEAM}"
        room = room_service.get_room(room_key)

        async def fake_infer(model, ctx):
            return _make_infer_response(tool_calls=[_send_msg_tool_call("general", "a message")])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            run_task = asyncio.create_task(scheduler.run())
            room_service.create_room(TEAM, "general", ["alice", "bob"], max_turns=2)
            room = room_service.get_room(room_key)
            for _ in range(20):
                if room.state.value == "idle":
                    break
                await asyncio.sleep(0.5)
            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=5.0)

        # 1 条公告 + 2轮×2人 = 5 条消息
        assert len(room.messages) == 5
