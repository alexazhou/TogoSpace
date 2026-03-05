import pytest
from unittest.mock import patch
from service.agent_tool_service import init, get_tools, execute_function
from service.chat_room_service import ChatRoom


class TestBuildTools:
    def test_build_tools_empty(self):
        with patch("service.agent_tool_service.load_enabled_functions", return_value=[]):
            init()
        assert get_tools() == []

    def test_build_tools_valid_function(self):
        with patch("service.agent_tool_service.load_enabled_functions", return_value=["get_weather"]):
            init()
        assert len(get_tools()) == 1
        assert get_tools()[0].function.name == "get_weather"

    def test_build_tools_unknown_function(self):
        with patch("service.agent_tool_service.load_enabled_functions", return_value=["nonexistent_func"]):
            init()
        assert get_tools() == []


class TestExecuteFunction:
    def setup_method(self):
        self.room = ChatRoom("test_room")

    def test_execute_basic(self):
        result = execute_function("get_weather", {"location": "北京", "unit": "celsius"})
        assert "25°C" in result

    def test_execute_with_context(self):
        context = {"chat_room": self.room, "agent_name": "agent1"}
        result = execute_function(
            "send_chat_msg",
            {"chat_windows_name": "room1", "msg": "hello"},
            context=context
        )
        assert result == "success"
        assert len(self.room.messages) == 1
        assert self.room.messages[0].content == "hello"

    def test_execute_not_found(self):
        with pytest.raises(ValueError):
            execute_function("nonexistent_function", {})

    def test_execute_bad_args(self):
        with pytest.raises(ValueError):
            execute_function("get_weather", {"bad_param": "value"})
