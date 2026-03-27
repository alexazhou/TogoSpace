import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from service.agentService.driver.nativeDriver import NativeAgentDriver
from service.agentService.driver.base import AgentDriverConfig
from util import llmApiUtil
from constants import OpenaiLLMApiRole

@pytest.fixture
def mock_host():
    host = MagicMock()
    host.key = "test_agent@test_team"
    host._infer = AsyncMock()
    host._execute_tool = AsyncMock()
    host.append_history_message = AsyncMock()
    return host

@pytest.fixture
def driver(mock_host):
    config = AgentDriverConfig(driver_type="native", options={})
    return NativeAgentDriver(mock_host, config)

@pytest.mark.asyncio
async def test_native_driver_run_until_reply_no_tool_calls(driver, mock_host):
    # Case: Assistant returns text only, no tool calls
    mock_host._infer.return_value = llmApiUtil.OpenAIMessage(
        role=OpenaiLLMApiRole.ASSISTANT,
        content="Hello world",
        tool_calls=None
    )
    
    room = MagicMock()
    result = await driver._run_until_reply(room)
    
    assert result is False
    mock_host._infer.assert_called_once()
    mock_host._execute_tool.assert_not_called()

@pytest.mark.asyncio
async def test_native_driver_run_until_reply_with_finish(driver, mock_host):
    # Case: Assistant calls finish_chat_turn
    mock_host._infer.return_value = llmApiUtil.OpenAIMessage(
        role=OpenaiLLMApiRole.ASSISTANT,
        content=None,
        tool_calls=[
            llmApiUtil.OpenAIToolCall(
                id="call_1",
                function={"name": "finish_chat_turn", "arguments": "{}"}
            )
        ]
    )
    
    room = MagicMock()
    result = await driver._run_until_reply(room)
    
    assert result is True
    mock_host._execute_tool.assert_called_once()

@pytest.mark.asyncio
async def test_native_driver_run_until_reply_max_calls(driver, mock_host):
    # Case: Assistant keeps calling tools but never finishes
    mock_host._infer.return_value = llmApiUtil.OpenAIMessage(
        role=OpenaiLLMApiRole.ASSISTANT,
        content=None,
        tool_calls=[
            llmApiUtil.OpenAIToolCall(
                id="call_1",
                function={"name": "some_tool", "arguments": "{}"}
            )
        ]
    )
    
    room = MagicMock()
    result = await driver._run_until_reply(room, max_function_calls=3)
    
    assert result is False
    assert mock_host._infer.call_count == 3
    assert mock_host._execute_tool.call_count == 3

@pytest.mark.asyncio
async def test_native_driver_run_chat_turn_retry_logic(driver, mock_host):
    # Case: First try fails (no tool call), retry should provide hint
    # 1. First _run_until_reply returns False
    # 2. Second _run_until_reply returns True
    
    room = MagicMock()
    
    with patch.object(driver, "_run_until_reply", side_effect=[False, True]):
        with patch("service.funcToolService.get_tools", return_value=[]):
            await driver.run_chat_turn(room, synced_count=0)
            
            assert driver._run_until_reply.call_count == 2
            mock_host.append_history_message.assert_called_once()
            args, _ = mock_host.append_history_message.call_args
            assert "你必须通过调用工具来行动" in args[0].content

@pytest.mark.asyncio
async def test_native_driver_run_chat_turn_all_retries_fail(driver, mock_host):
    room = MagicMock()
    
    with patch.object(driver, "_run_until_reply", return_value=False):
        with patch("service.funcToolService.get_tools", return_value=[]):
            await driver.run_chat_turn(room, synced_count=0)
            
            assert driver._run_until_reply.call_count == 3
            assert mock_host.append_history_message.call_count == 3
