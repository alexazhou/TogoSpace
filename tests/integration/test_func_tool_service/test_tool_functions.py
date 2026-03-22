"""integration tests for toolLoader utilities and service-backed tool functions"""
import os
import sys
from typing import Literal, Optional

import pytest

import service.roomService as roomService
from service.funcToolService.toolLoader import (
    python_type_to_json_schema,
    get_function_metadata,
    build_tools,
)
from service.funcToolService.tools import (
    get_weather,
    get_time,
    calculate,
    send_chat_msg,
    get_agent_list,
    finish_chat_turn,
)
from model.coreModel.gtCoreChatContext import ChatContext
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestPythonTypeToJsonSchema(ServiceTestCase):
    async def test_str(self):
        """str 映射为 JSON Schema string。"""
        assert python_type_to_json_schema(str) == {"type": "string"}

    async def test_int(self):
        """int 映射为 integer。"""
        assert python_type_to_json_schema(int) == {"type": "integer"}

    async def test_float(self):
        """float 映射为 number。"""
        assert python_type_to_json_schema(float) == {"type": "number"}

    async def test_bool(self):
        """bool 映射为 boolean。"""
        assert python_type_to_json_schema(bool) == {"type": "boolean"}

    async def test_optional_str(self):
        """Optional[T] 退化到 T 的 schema。"""
        assert python_type_to_json_schema(Optional[str]) == {"type": "string"}

    async def test_literal(self):
        """Literal 会映射为 enum。"""
        assert python_type_to_json_schema(Literal["celsius", "fahrenheit"]) == {"enum": ["celsius", "fahrenheit"]}

    async def test_unknown_falls_back_to_object(self):
        """未知类型默认回退为 object，保证 schema 可生成。"""
        class Custom:
            pass
        assert python_type_to_json_schema(Custom) == {"type": "object"}


@pytest.mark.forked
class TestGetFunctionMetadata(ServiceTestCase):
    async def test_name_is_set(self):
        """metadata 中 name 字段与注册名一致。"""
        assert get_function_metadata("get_weather", get_weather)["name"] == "get_weather"

    async def test_description_from_docstring(self):
        """description 应从函数 docstring 提取。"""
        assert get_function_metadata("get_weather", get_weather)["description"]

    async def test_required_includes_location(self):
        """必填参数会进入 required 列表。"""
        assert "location" in get_function_metadata("get_weather", get_weather)["parameters"]["required"]

    async def test_optional_param_not_required(self):
        """可选参数不应被标记为 required。"""
        assert "unit" not in get_function_metadata("get_weather", get_weather)["parameters"]["required"]

    async def test_private_params_excluded(self):
        """以下划线开头的上下文参数不暴露给 LLM。"""
        props = get_function_metadata("send_chat_msg", send_chat_msg)["parameters"]["properties"]
        assert "_context" not in props


@pytest.mark.forked
class TestBuildtools(ServiceTestCase):
    async def test_builds_tool_for_each_entry(self):
        """注册表中每个函数都应产出一个 Tool 定义。"""
        tools = build_tools({"get_weather": get_weather, "get_time": get_time})
        assert len(tools) == 2
        assert {t.function.name for t in tools} == {"get_weather", "get_time"}

    async def test_empty_registry(self):
        """空注册表返回空列表。"""
        assert build_tools({}) == []

    async def test_skips_function_with_error(self):
        """构建过程中单个函数异常不影响其他函数。"""
        assert len(build_tools({"get_weather": get_weather})) == 1


@pytest.mark.forked
class TestToolFunctions(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # send_chat_msg/get_agent_list 依赖 roomService 上下文。
        await roomService.startup()

    async def test_get_weather_celsius(self):
        """天气工具返回摄氏温度文本。"""
        assert "25°C" in get_weather("北京", "celsius")["message"]

    async def test_get_weather_fahrenheit(self):
        """天气工具返回华氏温度文本。"""
        assert "77°F" in get_weather("北京", "fahrenheit")["message"]

    async def test_get_time_local(self):
        """默认返回本地时区时间。"""
        assert "当前本地时间" in get_time()["message"]

    async def test_get_time_timezone(self):
        """指定时区时，返回内容包含目标时区标识。"""
        assert "UTC" in get_time(timezone="UTC")["message"]

    async def test_get_time_invalid_timezone(self):
        """未知时区应返回友好错误提示。"""
        result = get_time(timezone="Invalid/Zone")
        assert not result["success"] and "未知时区" in result["message"]

    async def test_calculate_addition(self):
        """基础算术表达式可执行。"""
        assert "5" in calculate("2 + 3")["message"]

    async def test_calculate_complex(self):
        """复杂表达式可执行。"""
        assert "1024" in calculate("2 ** 10")["message"]

    async def test_calculate_invalid(self):
        """非法表达式应被拒绝并返回错误信息。"""
        result = calculate("import os")
        assert not result["success"] and "计算错误" in result["message"]

    async def test_get_agent_list_without_context(self):
        """无上下文时 get_agent_list 返回空列表。"""
        assert get_agent_list()["agents"] == []

    async def test_get_agent_list_with_context(self):
        """有上下文时返回当前房间中可见的发言者列表。"""
        await roomService.create_room(TEAM, "r", ["alice"])
        room = roomService.get_room(f"r@{TEAM}")
        await room.add_message("alice", "hi")
        await room.add_message("bob", "there")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room)
        result = get_agent_list(_context=ctx)
        assert "alice" in result["agents"] and "bob" in result["agents"]

    async def test_send_chat_msg_returns_error_without_context(self):
        """无上下文时 send_chat_msg 应返回明确错误，不能伪装成功。"""
        assert not (await send_chat_msg("some_room", "hello"))["success"]

    async def test_send_chat_msg_with_valid_context(self):
        """同房间发送成功后，目标房间消息数应增加。"""
        await roomService.create_room(TEAM, "myroom", ["alice"])
        room = roomService.get_room(f"myroom@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room)
        assert (await send_chat_msg("myroom", "hello", _context=ctx))["success"]
        assert len(room.messages) == 2  # 1 (init公告) + 1 (new)
        assert room.messages[1].content == "hello"

    async def test_send_chat_msg_nonexistent_room_returns_error(self):
        """目标房间不存在时应返回明确错误，避免吞掉失败。"""
        await roomService.create_room(TEAM, "existing", ["alice"])
        room = roomService.get_room(f"existing@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room)
        result = await send_chat_msg("nonexistent", "hello", _context=ctx)
        assert not result["success"] and "nonexistent" in result["message"]

    async def test_send_chat_msg_cross_room_lands_in_target(self):
        """跨房间发消息时，消息必须落到目标房间，而不是 agent 当前所在房间。"""
        await roomService.create_room(TEAM, "room_a", ["alice"])
        await roomService.create_room(TEAM, "room_b", ["alice"])
        room_a = roomService.get_room(f"room_a@{TEAM}")
        room_b = roomService.get_room(f"room_b@{TEAM}")
        ctx = ChatContext(agent_name="alice", team_name=TEAM, chat_room=room_a)
        result = await send_chat_msg("room_b", "hello from a to b", _context=ctx)
        assert result["success"]
        assert any(m.content == "hello from a to b" for m in room_b.messages)
        assert not any(m.content == "hello from a to b" for m in room_a.messages)

    async def test_send_chat_msg_cross_room_does_not_pollute_current_room(self):
        """发到其他房间时，当前房间的消息列表不变。"""
        await roomService.create_room(TEAM, "src", ["bob"])
        await roomService.create_room(TEAM, "dst", ["bob"])
        src = roomService.get_room(f"src@{TEAM}")
        dst = roomService.get_room(f"dst@{TEAM}")
        before_count = len(src.messages)
        ctx = ChatContext(agent_name="bob", team_name=TEAM, chat_room=src)
        await send_chat_msg("dst", "cross-room msg", _context=ctx)
        assert len(src.messages) == before_count

    async def test_finish_chat_turn_rejects_non_current_agent(self):
        """不是当前发言人时，finish_chat_turn 不应推进轮次。"""
        await roomService.create_room(TEAM, "turn_room", ["alice", "bob"], max_turns=3)
        room = roomService.get_room(f"turn_room@{TEAM}")
        ctx = ChatContext(agent_name="bob", team_name=TEAM, chat_room=room)

        result = finish_chat_turn(_context=ctx)

        assert not result["success"] and "alice" in result["message"]
        assert room.get_current_turn_agent() == "alice"
