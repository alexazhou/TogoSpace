"""unit tests for service.agent_service — Agent class and module functions"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import service.message_bus as message_bus
import service.room_service as room_service
import service.agent_service as agent_service
from service.agent_service import Agent
from util.llm_api_util import LlmApiMessage
from constants import OpenaiLLMApiRole, TurnStatus, TurnCheckResult


# ---------- helpers ----------

def _make_llm_response(content="reply", tool_calls=None):
    msg = LlmApiMessage(role=OpenaiLLMApiRole.ASSISTANT, content=content, tool_calls=tool_calls)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(name, arguments, call_id="call_1"):
    from util.llm_api_util import ToolCall
    return ToolCall(id=call_id, function={"name": name, "arguments": json.dumps(arguments)})


@pytest.fixture(autouse=True)
def clean():
    message_bus.init()
    room_service.close_all()
    agent_service.close()
    yield
    agent_service.close()
    room_service.close_all()
    message_bus.stop()


# ---------- Agent._infer / Agent.chat ----------

class TestAgentChat:
    def setup_method(self):
        self.agent = Agent(name="test_agent", system_prompt="你是助手", model="qwen-plus")
        self.agent._history = [LlmApiMessage.text(OpenaiLLMApiRole.USER, "start")]

    @pytest.mark.asyncio
    async def test_chat_no_tool_calls_returns_message(self):
        with patch("service.agent_service.llm_service.infer", AsyncMock(return_value=_make_llm_response("你好"))):
            result = await self.agent.chat()
        assert result.content == "你好"

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_chat_max_function_calls_limits_loops(self):
        tool_call = _make_tool_call("get_weather", {"location": "北京"})
        mock_infer = AsyncMock(return_value=_make_llm_response(content=None, tool_calls=[tool_call]))
        executor = MagicMock(return_value="result")
        with patch("service.agent_service.llm_service.infer", mock_infer):
            await self.agent.chat(function_executor=executor, max_function_calls=3)
        assert mock_infer.call_count == 3

    @pytest.mark.asyncio
    async def test_chat_turn_checker_success_stops_loop(self):
        tool_call = _make_tool_call("send_chat_msg", {"chat_windows_name": "r", "msg": "hi"})
        responses = [_make_llm_response(content=None, tool_calls=[tool_call])]
        executor = MagicMock(return_value="success")

        def checker(msg):
            if msg.role == OpenaiLLMApiRole.TOOL:
                return TurnCheckResult(TurnStatus.SUCCESS)
            return TurnCheckResult(TurnStatus.CONTINUE)

        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            result = await self.agent.chat(function_executor=executor, turn_checker=checker)
        # SUCCESS 后返回空 assistant 消息
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_chat_turn_checker_error_injects_hint(self):
        """checker 返回 ERROR 时把 hint 注入 history 并重试"""
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

        # 注入 hint 后再次调用 infer
        assert mock_infer.call_count >= 2

    @pytest.mark.asyncio
    async def test_sync_room_appends_new_messages(self):
        message_bus.init()
        room_service.init("r")
        room = room_service.get_room("r")
        room.add_message("bob", "hello agent")
        self.agent.sync_room(room)
        assert any("bob" in (m.content or "") for m in self.agent._history)


# ---------- agent_service module functions ----------

class TestAgentServiceModule:
    def _setup_agents_and_rooms(self):
        agents_config = [
            {"name": "alice", "prompt_file": None, "model": "qwen-plus"},
            {"name": "bob",   "prompt_file": None, "model": "qwen-plus"},
        ]
        rooms_config = [
            {"name": "general", "agents": ["alice", "bob"]},
        ]
        room_service.init("general")
        with patch("service.agent_service.load_prompt", return_value="你是{participants}"):
            agent_service.init(agents_config, rooms_config)

    def test_init_creates_agents(self):
        self._setup_agents_and_rooms()
        assert agent_service.get_agent("alice") is not None
        assert agent_service.get_agent("bob") is not None

    def test_get_agents_returns_room_members(self):
        self._setup_agents_and_rooms()
        agents = agent_service.get_agents("general")
        names = [a.name for a in agents]
        assert set(names) == {"alice", "bob"}

    def test_get_all_rooms_for_agent(self):
        self._setup_agents_and_rooms()
        rooms = agent_service.get_all_rooms("alice")
        assert "general" in rooms

    def test_close_clears_agents(self):
        self._setup_agents_and_rooms()
        agent_service.close()
        with pytest.raises(KeyError):
            agent_service.get_agent("alice")

    @pytest.mark.asyncio
    async def test_run_turn_calls_agent_chat(self):
        self._setup_agents_and_rooms()
        room = room_service.get_room("general")
        room.add_message("system", "开始对话")
        alice = agent_service.get_agent("alice")

        tool_call = _make_tool_call("send_chat_msg", {"chat_windows_name": "general", "msg": "hi"})
        responses = [
            _make_llm_response(content=None, tool_calls=[tool_call]),
        ]

        import service.func_tool_service as fts
        fts.init()
        with patch("service.agent_service.llm_service.infer", AsyncMock(side_effect=responses)):
            await agent_service.run_turn(alice, "general", max_function_calls=5)

        # send_chat_msg 应把消息写入 general 房间
        assert any(m.content == "hi" for m in room.messages)
        fts.close()
