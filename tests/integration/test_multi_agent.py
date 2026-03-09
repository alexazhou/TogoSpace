"""integration tests — 验证多 Agent 完整对话流程（mock LLM，真实 service 层）"""
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import service.room_service as room_service
import service.agent_service as agent_service
import service.func_tool_service as func_tool_service
import service.scheduler_service as scheduler
from util.llm_api_util import LlmApiMessage, ToolCall
from constants import OpenaiLLMApiRole
from base import ServiceTestCase


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
        function={"name": "send_chat_msg", "arguments": json.dumps({"chat_windows_name": room_name, "msg": msg})},
    )


AGENTS_CONFIG = [
    {"name": "alice", "prompt_file": None, "model": "qwen-plus"},
    {"name": "bob",   "prompt_file": None, "model": "qwen-plus"},
]

ROOMS_CONFIG = [
    {"name": "general", "agents": ["alice", "bob"], "max_turns": 2},
]


class TestIntegrationMultiAgentChat(ServiceTestCase):
    def setup_method(self):
        super().setup_method()
        for rc in ROOMS_CONFIG:
            room_service.init(rc["name"])
        func_tool_service.init()
        with patch("service.agent_service.load_prompt", return_value="你是{participants}"):
            agent_service.init(AGENTS_CONFIG, ROOMS_CONFIG)
        scheduler.init(ROOMS_CONFIG)

    async def test_two_agents_exchange_messages(self):
        """alice 和 bob 各发一轮消息，general 房间应有消息。"""
        room = room_service.get_room("general")

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
            room.setup_turns(["alice", "bob"], max_turns=1)
            await asyncio.wait_for(scheduler.run(), timeout=5.0)

        agent_messages = [m for m in room.messages if m.sender_name != "system"]
        assert len(agent_messages) >= 2

    async def test_tool_call_result_appended_to_history(self):
        """验证 tool_call 结果被正确追加到 agent history。"""
        room = room_service.get_room("general")
        room.add_message("system", "开始聊天")

        alice = agent_service.get_agent("alice")
        tc = _send_msg_tool_call("general", "hello")
        responses = [
            _make_infer_response(tool_calls=[tc]),
            _make_infer_response(content="done"),
        ]
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await agent_service.run_turn(alice, "general", max_function_calls=5)

        tool_results = [m for m in alice._history if m.role == OpenaiLLMApiRole.TOOL]
        assert len(tool_results) >= 1
        assert tool_results[0].content == "success"

    async def test_turn_checker_forces_send_chat_msg(self):
        """直接输出文字时 turn_checker 应注入 hint，迫使 agent 改用工具。"""
        room = room_service.get_room("general")
        room.add_message("system", "开始聊天")

        alice = agent_service.get_agent("alice")
        tc = _send_msg_tool_call("general", "最终消息")
        responses = [
            _make_infer_response(content="我直接回复"),
            _make_infer_response(tool_calls=[tc]),
        ]
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await agent_service.run_turn(alice, "general", max_function_calls=5)

        assert any(m.content == "最终消息" for m in room.messages)

    async def test_scheduler_terminates_after_max_turns(self):
        """max_turns 用尽后，scheduler.run() 应正常结束，消息数等于轮次 × agent 数。"""
        room = room_service.get_room("general")

        async def fake_infer(model, ctx):
            return _make_infer_response(tool_calls=[_send_msg_tool_call("general", "a message")])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            await asyncio.wait_for(scheduler.run(), timeout=10.0)

        assert len(room.messages) == 4
