"""integration tests for service.func_tool_service — 需要 func_tool_service.startup()"""
import service.func_tool_service as func_tool_service
import service.room_service as room_service
from model.chat_context import ChatContext
from ...base import ServiceTestCase

TEAM = "test_team"


class TestFuncToolServiceInit(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 这组用例只验证工具注册生命周期，不依赖房间状态。
        await cls.areset_services()
        await func_tool_service.startup()

    async def test_init_loads_tools(self):
        """startup 后工具注册表应非空。"""
        assert len(func_tool_service.get_tools()) > 0

    async def test_close_clears_tools(self):
        """shutdown 后工具注册表应被清空。"""
        func_tool_service.shutdown()
        assert func_tool_service.get_tools() == []


class TestRunToolCall(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # send_chat_msg 依赖房间上下文，因此同时初始化 room + tool service。
        await cls.areset_services()
        await room_service.startup()
        await func_tool_service.startup()

    async def test_run_tool_call_basic(self):
        """正常 JSON 入参可成功执行工具函数。"""
        result = await func_tool_service.run_tool_call("get_weather", '{"location": "北京", "unit": "celsius"}')
        assert "25°C" in result

    async def test_run_tool_call_invalid_json(self):
        """非法 JSON 不应抛异常，应返回可读错误文本。"""
        result = await func_tool_service.run_tool_call("get_weather", "not json")
        assert "失败" in result or "error" in result.lower() or "Error" in result

    async def test_run_tool_call_unknown_function(self):
        """未知函数名应返回失败信息。"""
        result = await func_tool_service.run_tool_call("nonexistent", "{}")
        assert "失败" in result or "not found" in result.lower()

    async def test_run_tool_call_with_context(self):
        """上下文注入场景：send_chat_msg 能在上下文房间成功落消息。"""
        await room_service.create_room(TEAM, "ctx_room", ["alice"])
        room = room_service.get_room(f"ctx_room@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room, get_room=room_service.get_room)
        result = await func_tool_service.run_tool_call(
            "send_chat_msg",
            '{"room_name": "ctx_room", "msg": "test"}',
            context=ctx,
        )
        assert result == "success"

    def test_run_tool_call_with_missing_room_returns_error(self):
        """目标房间不存在时，工具调用结果应显式失败。"""
        room_service.create_room(TEAM, "ctx_room_missing", ["alice"])
        room = room_service.get_room(f"ctx_room_missing@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room, get_room=room_service.get_room)
        result = func_tool_service.run_tool_call(
            "send_chat_msg",
            '{"room_name": "missing_room", "msg": "test"}',
            context=ctx,
        )
        assert result == f"error: room not found: missing_room@{TEAM}"
