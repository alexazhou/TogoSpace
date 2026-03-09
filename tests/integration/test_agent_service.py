"""integration tests for service.agent_service — Agent class and module functions"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import service.room_service as room_service
import service.agent_service as agent_service
import service.func_tool_service as func_tool_service
from service.agent_service import Agent
from util.llm_api_util import LlmApiMessage, ToolCall
from constants import OpenaiLLMApiRole, TurnStatus, TurnCheckResult
from base import ServiceTestCase


def _make_llm_response(content="reply", tool_calls=None):
    msg = LlmApiMessage(role=OpenaiLLMApiRole.ASSISTANT, content=content, tool_calls=tool_calls)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(name, arguments, call_id="call_1"):
    return ToolCall(id=call_id, function={"name": name, "arguments": json.dumps(arguments)})


class TestAgentChat(ServiceTestCase):
    def setup_method(self):
        super().setup_method()
        self.agent = Agent(name="test_agent", system_prompt="你是助手", model="qwen-plus")
        self.agent._history = [LlmApiMessage.text(OpenaiLLMApiRole.USER, "start")]

    async def test_chat_no_tool_calls_returns_message(self):
        with patch("service.agent_service.llm_service.infer", AsyncMock(return_value=_make_llm_response("你好"))):
            result = await self.agent.chat()
        assert result.content == "你好"

    async def test_chat_with_tool_call_executes_executor(self):
        tool_call = _make_tool_call("get_weather", {"location": "北京", "unit": "celsius"})
        responses = [
            _make_llm_response(content=None, tool_calls=[tool_call]),
            _make_llm_response("天气不错"),
        ]
        executor = MagicMock(return_value="25°C")
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            result = await self.agent.chat(function_executor=executor)
        executor.assert_called_once()
        assert result.content == "天气不错"

    async def test_chat_max_function_calls_limits_loops(self):
        tool_call = _make_tool_call("get_weather", {"location": "北京"})
        mock_infer = AsyncMock(return_value=_make_llm_response(content=None, tool_calls=[tool_call]))
        executor = MagicMock(return_value="result")
        with patch("service.agent_service.llm_service.infer", mock_infer):
            await self.agent.chat(function_executor=executor, max_function_calls=3)
        assert mock_infer.call_count == 3

    async def test_chat_turn_checker_success_stops_loop(self):
        tool_call = _make_tool_call("send_chat_msg", {"chat_windows_name": "r", "msg": "hi"})
        executor = MagicMock(return_value="success")

        def checker(msg):
            if msg.role == OpenaiLLMApiRole.TOOL:
                return TurnCheckResult(TurnStatus.SUCCESS)
            return TurnCheckResult(TurnStatus.CONTINUE)

        with patch("service.agent_service.llm_service.infer",
                   AsyncMock(side_effect=[_make_llm_response(content=None, tool_calls=[tool_call])])):
            result = await self.agent.chat(function_executor=executor, turn_checker=checker)
        assert result.content == ""

    async def test_chat_turn_checker_error_injects_hint(self):
        call_count = {"n": 0}

        def checker(msg):
            if msg.tool_calls:
                return TurnCheckResult(TurnStatus.CONTINUE)
            call_count["n"] += 1
            if call_count["n"] < 2:
                return TurnCheckResult(TurnStatus.ERROR, "请使用工具")
            return TurnCheckResult(TurnStatus.SUCCESS)

        mock_infer = AsyncMock(return_value=_make_llm_response("直接回复"))
        with patch("service.agent_service.llm_service.infer", mock_infer):
            await self.agent.chat(turn_checker=checker, max_function_calls=5)
        assert mock_infer.call_count >= 2

    async def test_sync_room_appends_new_messages(self):
        room_service.init("r")
        room = room_service.get_room("r")
        room.add_message("bob", "hello agent")
        self.agent.sync_room(room)
        assert any("bob" in (m.content or "") for m in self.agent._history)


class TestAgentServiceModule(ServiceTestCase):
    def _setup_agents_and_rooms(self):
        agents_config = [
            {"name": "alice", "prompt_file": None, "model": "qwen-plus"},
            {"name": "bob",   "prompt_file": None, "model": "qwen-plus"},
        ]
        rooms_config = [{"name": "general", "agents": ["alice", "bob"]}]
        room_service.init("general")
        with patch("service.agent_service.load_prompt", return_value="你是{participants}"):
            agent_service.init(agents_config, rooms_config)

    def test_init_creates_agents(self):
        self._setup_agents_and_rooms()
        assert agent_service.get_agent("alice") is not None
        assert agent_service.get_agent("bob") is not None

    def test_get_agents_returns_room_members(self):
        self._setup_agents_and_rooms()
        assert {a.name for a in agent_service.get_agents("general")} == {"alice", "bob"}

    def test_get_all_rooms_for_agent(self):
        self._setup_agents_and_rooms()
        assert "general" in agent_service.get_all_rooms("alice")

    def test_close_clears_agents(self):
        self._setup_agents_and_rooms()
        agent_service.close()
        with pytest.raises(KeyError):
            agent_service.get_agent("alice")

    async def test_run_turn_sends_message_to_room(self):
        self._setup_agents_and_rooms()
        room = room_service.get_room("general")
        room.add_message("system", "开始对话")
        alice = agent_service.get_agent("alice")

        tool_call = _make_tool_call("send_chat_msg", {"chat_windows_name": "general", "msg": "hi"})
        func_tool_service.init()
        with patch("service.agent_service.llm_service.infer",
                   AsyncMock(side_effect=[_make_llm_response(content=None, tool_calls=[tool_call])])):
            await agent_service.run_turn(alice, "general", max_function_calls=5)

        assert any(m.content == "hi" for m in room.messages)
