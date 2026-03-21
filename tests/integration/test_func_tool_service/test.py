"""integration tests for service.func_tool_service — 需要 func_tool_service.startup()"""
import os
import sys

import pytest

import service.func_tool_service as func_tool_service
import service.room_service as room_service
from model.chat_context import ChatContext
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestFuncToolServiceInit(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 这组用例只验证工具注册生命周期，不依赖房间状态。
        await func_tool_service.startup()

    async def test_init_loads_tools(self):
        """startup 后工具注册表应非空。"""
        assert len(func_tool_service.get_tools()) > 0

    async def test_close_clears_tools(self):
        """shutdown 后工具注册表应被清空。"""
        func_tool_service.shutdown()
        assert func_tool_service.get_tools() == []


@pytest.mark.forked
class TestRunToolCall(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # send_chat_msg 依赖房间上下文，因此同时初始化 room + tool service。
        await room_service.startup()
        await func_tool_service.startup()

    async def _run(self, name, args, **kw):
        import json
        return json.loads(await func_tool_service.run_tool_call(name, args, **kw))

    async def test_run_tool_call_basic(self):
        """正常 JSON 入参可成功执行工具函数。"""
        result = await self._run("get_weather", '{"location": "北京", "unit": "celsius"}')
        assert result["success"] and "25°C" in result["message"]

    async def test_run_tool_call_invalid_json(self):
        """非法 JSON 不应抛异常，应返回可读错误文本。"""
        result = await self._run("get_weather", "not json")
        assert not result["success"]

    async def test_run_tool_call_unknown_function(self):
        """未知函数名应返回失败信息。"""
        result = await self._run("nonexistent", "{}")
        assert not result["success"]

    async def test_run_tool_call_with_context(self):
        """上下文注入场景：send_chat_msg 能在上下文房间成功落消息。"""
        await room_service.create_room(TEAM, "ctx_room", ["alice"])
        room = room_service.get_room(f"ctx_room@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room)
        result = await self._run("send_chat_msg", '{"room_name": "ctx_room", "msg": "test"}', context=ctx)
        assert result["success"] and "消息已发送" in result["message"]

    async def test_run_tool_call_with_missing_room_returns_error(self):
        """目标房间不存在时，应返回错误信息。"""
        await room_service.create_room(TEAM, "ctx_room_missing", ["alice"])
        room = room_service.get_room(f"ctx_room_missing@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room)
        result = await self._run("send_chat_msg", '{"room_name": "missing_room", "msg": "test"}', context=ctx)
        assert not result["success"]
        assert not any(message.content == "test" for message in room.messages)
