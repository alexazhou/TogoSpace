"""integration tests for service.func_tool_service — 需要 func_tool_service.init()"""
import service.func_tool_service as func_tool_service
import service.room_service as room_service
from model.chat_context import ChatContext
from base import ServiceTestCase


class TestFuncToolServiceInit(ServiceTestCase):
    def test_init_loads_tools(self):
        func_tool_service.init()
        assert len(func_tool_service.get_tools()) > 0

    def test_close_clears_tools(self):
        func_tool_service.init()
        func_tool_service.close()
        assert func_tool_service.get_tools() == []


class TestRunToolCall(ServiceTestCase):
    def setup_method(self):
        super().setup_method()
        func_tool_service.init()

    def test_run_tool_call_basic(self):
        result = func_tool_service.run_tool_call("get_weather", '{"location": "北京", "unit": "celsius"}')
        assert "25°C" in result

    def test_run_tool_call_invalid_json(self):
        result = func_tool_service.run_tool_call("get_weather", "not json")
        assert "失败" in result or "error" in result.lower() or "Error" in result

    def test_run_tool_call_unknown_function(self):
        result = func_tool_service.run_tool_call("nonexistent", "{}")
        assert "失败" in result or "not found" in result.lower()

    def test_run_tool_call_with_context(self):
        room_service.init("ctx_room")
        room = room_service.get_room("ctx_room")
        ctx = ChatContext(agent_name="alice", chat_room=room, get_room=room_service.get_room)
        result = func_tool_service.run_tool_call(
            "send_chat_msg",
            '{"chat_windows_name": "ctx_room", "msg": "test"}',
            context=ctx,
        )
        assert result == "success"
