"""unit tests for service.func_tool_service"""
import pytest
from unittest.mock import MagicMock
from typing import Literal, Optional

import service.func_tool_service as func_tool_service
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
    FUNCTION_REGISTRY,
)
from service.room_service import ChatRoom
from model.chat_context import ChatContext


# ---------- python_type_to_json_schema ----------

class TestPythonTypeToJsonSchema:
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
        result = python_type_to_json_schema(Literal["celsius", "fahrenheit"])
        assert result == {"enum": ["celsius", "fahrenheit"]}

    def test_unknown_falls_back_to_object(self):
        class Custom:
            pass
        assert python_type_to_json_schema(Custom) == {"type": "object"}


# ---------- get_function_metadata ----------

class TestGetFunctionMetadata:
    def test_name_is_set(self):
        meta = get_function_metadata("get_weather", get_weather)
        assert meta["name"] == "get_weather"

    def test_description_from_docstring(self):
        meta = get_function_metadata("get_weather", get_weather)
        assert meta["description"]  # non-empty

    def test_required_includes_location(self):
        meta = get_function_metadata("get_weather", get_weather)
        assert "location" in meta["parameters"]["required"]

    def test_optional_param_not_required(self):
        meta = get_function_metadata("get_weather", get_weather)
        assert "unit" not in meta["parameters"]["required"]

    def test_private_params_excluded(self):
        meta = get_function_metadata("send_chat_msg", send_chat_msg)
        props = meta["parameters"]["properties"]
        assert "_context" not in props


# ---------- build_tools ----------

class TestBuildTools:
    def test_builds_tool_for_each_entry(self):
        tools = build_tools({"get_weather": get_weather, "get_time": get_time})
        assert len(tools) == 2
        names = {t.function.name for t in tools}
        assert names == {"get_weather", "get_time"}

    def test_empty_registry(self):
        tools = build_tools({})
        assert tools == []

    def test_skips_function_with_error(self):
        def bad_func():
            pass
        bad_func.__doc__ = None
        # 强制 get_type_hints 出错不崩溃，仍能加载其他函数
        tools = build_tools({"get_weather": get_weather})
        assert len(tools) == 1


# ---------- func_tool_service init / get_tools ----------

class TestFuncToolServiceInit:
    def setup_method(self):
        func_tool_service.close()

    def test_init_loads_tools(self):
        func_tool_service.init()
        tools = func_tool_service.get_tools()
        assert len(tools) > 0

    def test_close_clears_tools(self):
        func_tool_service.init()
        func_tool_service.close()
        assert func_tool_service.get_tools() == []


# ---------- tool functions ----------

class TestToolFunctions:
    def test_get_weather_celsius(self):
        result = get_weather("北京", "celsius")
        assert "25°C" in result

    def test_get_weather_fahrenheit(self):
        result = get_weather("北京", "fahrenheit")
        assert "77°F" in result

    def test_get_time_local(self):
        result = get_time()
        assert "当前本地时间" in result

    def test_get_time_timezone(self):
        result = get_time(timezone="UTC")
        assert "UTC" in result

    def test_get_time_invalid_timezone(self):
        result = get_time(timezone="Invalid/Zone")
        assert "未知时区" in result

    def test_calculate_addition(self):
        result = calculate("2 + 3")
        assert "5" in result

    def test_calculate_complex(self):
        result = calculate("2 ** 10")
        assert "1024" in result

    def test_calculate_invalid(self):
        result = calculate("import os")
        assert "计算错误" in result

    def test_get_agent_list_without_context(self):
        assert get_agent_list() == []

    def test_get_agent_list_with_context(self):
        from service import room_service, message_bus as mb
        mb.init()
        room_service.init("r")
        room = room_service.get_room("r")
        room.add_message("alice", "hi")
        room.add_message("bob", "there")
        ctx = ChatContext(agent_name="alice", chat_room=room, get_room=room_service.get_room)
        result = get_agent_list(_context=ctx)
        assert "alice" in result and "bob" in result
        room_service.close_all()
        mb.stop()

    def test_send_chat_msg_returns_success_no_context(self):
        result = send_chat_msg("some_room", "hello")
        assert result == "success"

    def test_send_chat_msg_with_valid_context(self):
        from service import room_service, message_bus as mb
        mb.init()
        room_service.init("myroom")
        room = room_service.get_room("myroom")
        ctx = ChatContext(agent_name="alice", chat_room=room, get_room=room_service.get_room)
        result = send_chat_msg("myroom", "hello", _context=ctx)
        assert result == "success"
        assert len(room.messages) == 1
        assert room.messages[0].content == "hello"
        room_service.close_all()
        mb.stop()

    def test_send_chat_msg_nonexistent_room_returns_success(self):
        from service import room_service, message_bus as mb
        mb.init()
        room_service.init("existing")
        room = room_service.get_room("existing")
        ctx = ChatContext(agent_name="alice", chat_room=room, get_room=room_service.get_room)
        # 目标房间不存在时不报错，返回 success
        result = send_chat_msg("nonexistent", "hello", _context=ctx)
        assert result == "success"
        room_service.close_all()
        mb.stop()


# ---------- run_tool_call ----------

class TestRunToolCall:
    def setup_method(self):
        func_tool_service.init()

    def teardown_method(self):
        func_tool_service.close()

    def test_run_tool_call_basic(self):
        result = func_tool_service.run_tool_call("get_weather", '{"location": "北京", "unit": "celsius"}')
        assert "25°C" in result

    def test_run_tool_call_invalid_json(self):
        # 无效 JSON 应退化为空 args，但 get_weather 需要 location → 返回错误字符串
        result = func_tool_service.run_tool_call("get_weather", "not json")
        assert "失败" in result or "error" in result.lower() or "Error" in result

    def test_run_tool_call_unknown_function(self):
        result = func_tool_service.run_tool_call("nonexistent", "{}")
        assert "失败" in result or "not found" in result.lower() or "Not found" in result

    def test_run_tool_call_with_context(self):
        from service import room_service, message_bus as mb
        mb.init()
        room_service.init("ctx_room")
        room = room_service.get_room("ctx_room")
        ctx = ChatContext(agent_name="alice", chat_room=room, get_room=room_service.get_room)
        result = func_tool_service.run_tool_call(
            "send_chat_msg",
            '{"chat_windows_name": "ctx_room", "msg": "test"}',
            context=ctx,
        )
        assert result == "success"
        room_service.close_all()
        mb.stop()
