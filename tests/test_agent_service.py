import pytest
from unittest.mock import AsyncMock, MagicMock
from service.agent_service import Agent


def make_response(content="reply", tool_calls=None):
    """构造模拟 API response"""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls or []
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


def make_tool_call(name, arguments, call_id="call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function = {"name": name, "arguments": arguments}
    return tc


class TestAgent:
    def setup_method(self):
        self.agent = Agent(name="test_agent", system_prompt="你是一个助手", model="qwen-plus")

    @pytest.mark.asyncio
    async def test_generate_response_returns_content(self):
        api_client = AsyncMock()
        api_client.call_chat_completion.return_value = make_response("你好")
        result = await self.agent.generate_response(api_client, [{"role": "user", "content": "hi"}])
        assert result == "你好"

    @pytest.mark.asyncio
    async def test_generate_with_function_calling_no_tool_calls(self):
        api_client = AsyncMock()
        api_client.call_chat_completion.return_value = make_response("no tools here")
        content, calls = await self.agent.generate_with_function_calling(api_client, [])
        assert content == "no tools here"
        assert calls == []

    @pytest.mark.asyncio
    async def test_generate_with_function_calling_with_tool_call(self):
        api_client = AsyncMock()
        tool_call = make_tool_call("get_weather", {"location": "北京", "unit": "celsius"})
        api_client.call_chat_completion.side_effect = [
            make_response("thinking", tool_calls=[tool_call]),
            make_response("天气不错"),
        ]

        executor = MagicMock(return_value="25°C，晴天")
        content, calls = await self.agent.generate_with_function_calling(
            api_client, [], function_executor=executor
        )

        assert content == "天气不错"
        assert len(calls) == 1
        assert calls[0]["function"] == "get_weather"
        assert calls[0]["result"] == "25°C，晴天"
        executor.assert_called_once_with("get_weather", {"location": "北京", "unit": "celsius"})

    @pytest.mark.asyncio
    async def test_generate_with_function_calling_max_calls_reached(self):
        api_client = AsyncMock()
        tool_call = make_tool_call("get_weather", {"location": "北京"})
        api_client.call_chat_completion.return_value = make_response("loop", tool_calls=[tool_call])

        executor = MagicMock(return_value="result")
        content, calls = await self.agent.generate_with_function_calling(
            api_client, [], function_executor=executor, max_function_calls=2
        )

        # 循环 2 次后停止，api_client 调用 2 次
        assert api_client.call_chat_completion.call_count == 2
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_generate_with_function_calling_no_executor(self):
        api_client = AsyncMock()
        tool_call = make_tool_call("get_weather", {"location": "北京"})
        api_client.call_chat_completion.side_effect = [
            make_response("thinking", tool_calls=[tool_call]),
            make_response("final"),
        ]

        content, calls = await self.agent.generate_with_function_calling(api_client, [])
        assert calls[0]["result"] == "函数执行器未配置"

    @pytest.mark.asyncio
    async def test_generate_with_function_calling_json_args(self):
        """验证 function_args 为 JSON 字符串时能正确解析"""
        api_client = AsyncMock()
        import json
        tool_call = make_tool_call("get_weather", json.dumps({"location": "上海", "unit": "fahrenheit"}))
        api_client.call_chat_completion.side_effect = [
            make_response("thinking", tool_calls=[tool_call]),
            make_response("done"),
        ]

        executor = MagicMock(return_value="77°F")
        content, calls = await self.agent.generate_with_function_calling(
            api_client, [], function_executor=executor
        )

        executor.assert_called_once_with("get_weather", {"location": "上海", "unit": "fahrenheit"})
        assert calls[0]["arguments"] == {"location": "上海", "unit": "fahrenheit"}
