"""integration tests — 验证多 Agent 完整对话流程（mock LLM，真实 service 层）"""
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import service.message_bus as message_bus
import service.room_service as room_service
import service.agent_service as agent_service
import service.func_tool_service as func_tool_service
import service.scheduler_service as scheduler
from util.llm_api_util import LlmApiMessage, ToolCall
from constants import OpenaiLLMApiRole


# ---------- helpers ----------

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


@pytest.fixture(autouse=True)
def clean():
    message_bus.init()
    room_service.close_all()
    agent_service.close()
    func_tool_service.close()
    scheduler.stop()
    yield
    scheduler.stop()
    func_tool_service.close()
    agent_service.close()
    room_service.close_all()
    message_bus.stop()


def _boot():
    """初始化所有 service，不依赖外部文件。"""
    for rc in ROOMS_CONFIG:
        room_service.init(rc["name"])
    func_tool_service.init()
    with patch("service.agent_service.load_prompt", return_value="你是{participants}"):
        agent_service.init(AGENTS_CONFIG, ROOMS_CONFIG)
    scheduler.init(ROOMS_CONFIG)


class TestIntegrationMultiAgentChat:
    @pytest.mark.asyncio
    async def test_two_agents_exchange_messages(self):
        """alice 和 bob 各发一轮消息，general 房间应有 2 条消息。"""
        _boot()
        room = room_service.get_room("general")

        alice_reply = _send_msg_tool_call("general", "你好，bob！")
        bob_reply   = _send_msg_tool_call("general", "你好，alice！")

        call_seq = {
            "alice": [
                _make_infer_response(tool_calls=[alice_reply]),
            ],
            "bob": [
                _make_infer_response(tool_calls=[bob_reply]),
            ],
        }

        async def fake_infer(model, ctx):
            # 根据 system_prompt 判断是哪个 agent
            name = next(
                (n for n in call_seq if n in ctx.system_prompt),
                None,
            )
            if name and call_seq[name]:
                return call_seq[name].pop(0)
            return _make_infer_response(tool_calls=[_send_msg_tool_call("general", "...")])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            room.setup_turns(["alice", "bob"], max_turns=1)
            await asyncio.wait_for(scheduler.run(), timeout=5.0)

        agent_messages = [m for m in room.messages if m.sender_name not in ("system",)]
        assert len(agent_messages) >= 2

    @pytest.mark.asyncio
    async def test_tool_call_result_appended_to_history(self):
        """验证 tool_call 结果被正确追加到 agent history。"""
        _boot()
        room = room_service.get_room("general")
        room.add_message("system", "开始聊天")

        alice = agent_service.get_agent("alice")
        tc = _send_msg_tool_call("general", "hello")
        responses = [
            _make_infer_response(tool_calls=[tc]),
            # 第二次 infer 不再有 tool_calls，结束循环
            _make_infer_response(content="done"),
        ]
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await agent_service.run_turn(alice, "general", max_function_calls=5)

        tool_results = [m for m in alice._history if m.role == OpenaiLLMApiRole.TOOL]
        assert len(tool_results) >= 1
        assert tool_results[0].content == "success"

    @pytest.mark.asyncio
    async def test_turn_checker_forces_send_chat_msg(self):
        """如果 agent 直接输出文字而不调用 send_chat_msg，turn_checker 应注入 hint 重试。"""
        _boot()
        room = room_service.get_room("general")
        room.add_message("system", "开始聊天")

        alice = agent_service.get_agent("alice")
        tc = _send_msg_tool_call("general", "最终消息")
        responses = [
            _make_infer_response(content="我直接回复"),   # 违规：直接输出文字
            _make_infer_response(tool_calls=[tc]),         # 收到 hint 后改用工具
        ]
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await agent_service.run_turn(alice, "general", max_function_calls=5)

        # 房间里应该有 "最终消息"
        assert any(m.content == "最终消息" for m in room.messages)

    @pytest.mark.asyncio
    async def test_scheduler_terminates_after_max_turns(self):
        """max_turns 用尽后，scheduler.run() 应正常结束，且消息数等于轮次 × agent 数。"""
        _boot()
        room = room_service.get_room("general")

        async def fake_infer(model, ctx):
            # 通过 system_prompt 中是否含有 "bob" 在 participants 里来区分
            # alice 的 prompt = "你是bob、alice" 中排序后第一个是 bob
            # 直接用 agent name 传入的 model 字段区分更可靠，但这里 model 都一样
            # 改为：每次都发送一条固定消息，关键是测终止逻辑
            tc = _send_msg_tool_call("general", "a message")
            return _make_infer_response(tool_calls=[tc])

        with patch("service.agent_service.llm_service.infer", fake_infer):
            await asyncio.wait_for(scheduler.run(), timeout=10.0)

        # scheduler.run() 内部调用 setup_turns(max_turns=2)，2 轮 × 2 agents = 4 条消息
        assert len(room.messages) == 4
