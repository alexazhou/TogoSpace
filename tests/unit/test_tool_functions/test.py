"""unit tests for tool_loader utilities and individual tool functions"""
from typing import Literal, Optional

import service.room_service as room_service
from service.func_tool_service.tool_loader import (
    python_type_to_json_schema,
    get_function_metadata,
    build_tools,
)
from service.func_tool_service.tools import (
    get_weather,
    get_time,
    calculate,
    send_chat_msg,
    get_agent_list,
)
from model.chat_context import ChatContext
from ...base import ServiceTestCase

TEAM = "test_team"


class TestPythonTypeToJsonSchema(ServiceTestCase):
    def test_str(self):
        assert python_type_to_json_schema(str) == {"type": "string"}

    def test_int(self):
        assert python_type_to_json_schema(int) == {"type": "integer"}

    def test_float(self):
        assert python_type_to_json_schema(float) == {"type": "number"}

    def test_bool(self):
        assert python_type_to_json_schema(bool) == {"type": "boolean"}

    def test_optional_str(self):
        assert python_type_to_json_schema(Optional[str]) == {"type": "string"}

    def test_literal(self):
        assert python_type_to_json_schema(Literal["celsius", "fahrenheit"]) == {"enum": ["celsius", "fahrenheit"]}

    def test_unknown_falls_back_to_object(self):
        class Custom:
            pass
        assert python_type_to_json_schema(Custom) == {"type": "object"}


class TestGetFunctionMetadata(ServiceTestCase):
    def test_name_is_set(self):
        assert get_function_metadata("get_weather", get_weather)["name"] == "get_weather"

    def test_description_from_docstring(self):
        assert get_function_metadata("get_weather", get_weather)["description"]

    def test_required_includes_location(self):
        assert "location" in get_function_metadata("get_weather", get_weather)["parameters"]["required"]

    def test_optional_param_not_required(self):
        assert "unit" not in get_function_metadata("get_weather", get_weather)["parameters"]["required"]

    def test_private_params_excluded(self):
        props = get_function_metadata("send_chat_msg", send_chat_msg)["parameters"]["properties"]
        assert "_context" not in props


class TestBuildTools(ServiceTestCase):
    def test_builds_tool_for_each_entry(self):
        tools = build_tools({"get_weather": get_weather, "get_time": get_time})
        assert len(tools) == 2
        assert {t.function.name for t in tools} == {"get_weather", "get_time"}

    def test_empty_registry(self):
        assert build_tools({}) == []

    def test_skips_function_with_error(self):
        assert len(build_tools({"get_weather": get_weather})) == 1


class TestToolFunctions(ServiceTestCase):
    def setup_method(self):
        super().setup_method()
        room_service.init()

    def test_get_weather_celsius(self):
        assert "25°C" in get_weather("北京", "celsius")

    def test_get_weather_fahrenheit(self):
        assert "77°F" in get_weather("北京", "fahrenheit")

    def test_get_time_local(self):
        assert "当前本地时间" in get_time()

    def test_get_time_timezone(self):
        assert "UTC" in get_time(timezone="UTC")

    def test_get_time_invalid_timezone(self):
        assert "未知时区" in get_time(timezone="Invalid/Zone")

    def test_calculate_addition(self):
        assert "5" in calculate("2 + 3")

    def test_calculate_complex(self):
        assert "1024" in calculate("2 ** 10")

    def test_calculate_invalid(self):
        assert "计算错误" in calculate("import os")

    def test_get_agent_list_without_context(self):
        assert get_agent_list() == []

    def test_get_agent_list_with_context(self):
        room_service.create_room(TEAM, "r", ["alice"])
        room = room_service.get_room(f"r@{TEAM}")
        room.add_message("alice", "hi")
        room.add_message("bob", "there")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room, get_room=room_service.get_room)
        result = get_agent_list(_context=ctx)
        assert "alice" in result and "bob" in result

    def test_send_chat_msg_returns_success_no_context(self):
        assert send_chat_msg("some_room", "hello") == "success"

    def test_send_chat_msg_with_valid_context(self):
        room_service.create_room(TEAM, "myroom", ["alice"])
        room = room_service.get_room(f"myroom@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room, get_room=room_service.get_room)
        assert send_chat_msg("myroom", "hello", _context=ctx) == "success"
        assert len(room.messages) == 2  # 1 (init公告) + 1 (new)
        assert room.messages[1].content == "hello"

    def test_send_chat_msg_nonexistent_room_returns_success(self):
        room_service.create_room(TEAM, "existing", ["alice"])
        room = room_service.get_room(f"existing@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room, get_room=room_service.get_room)
        assert send_chat_msg("nonexistent", "hello", _context=ctx) == "success"
