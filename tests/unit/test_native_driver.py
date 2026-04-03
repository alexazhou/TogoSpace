from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryTag
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver.nativeDriver import NativeAgentDriver
from service.agentService.toolRegistry import AgentToolRegistry
from service.roomService import ToolCallContext
from util import llmApiUtil


def _make_tool(name: str) -> llmApiUtil.OpenAITool:
    return llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name=name,
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[]),
        )
    )


@pytest.fixture
def mock_host():
    host = MagicMock()
    host.gt_agent = MagicMock()
    host.gt_agent.id = 1
    host.tool_registry = AgentToolRegistry()
    return host


@pytest.fixture
def driver(mock_host):
    config = AgentDriverConfig(driver_type="native", options={})
    return NativeAgentDriver(mock_host, config)


@pytest.mark.asyncio
async def test_native_driver_setup_registers_tools(driver, mock_host):
    send_tool = _make_tool("send_chat_msg")
    finish_tool = _make_tool("finish_chat_turn")

    run_tool_call = AsyncMock(return_value={"success": True})
    with patch("service.funcToolService.get_tools", return_value=[send_tool, finish_tool]), patch(
        "service.funcToolService.run_tool_call",
        run_tool_call,
    ):
        await driver.startup()
        context = ToolCallContext(
            agent_name="alice",
            team_id=1,
            chat_room=MagicMock(),
        )
        result = await mock_host.tool_registry.execute_tool_call(
            llmApiUtil.OpenAIToolCall(
                id="tool_1",
                function={"name": "finish_chat_turn", "arguments": "{}"},
            ),
            context=context,
        )

    setup = driver.turn_setup

    assert setup.max_retries == 3
    assert "finish_chat_turn" in setup.hint_prompt

    exported_names = [t.function.name for t in mock_host.tool_registry.export_openai_tools()]
    assert exported_names == ["send_chat_msg", "finish_chat_turn"]

    run_tool_call.assert_called_once()
    called_args, called_context = run_tool_call.call_args.args
    assert called_args == "{}"
    assert called_context.agent_name == "alice"
    assert called_context.team_id == 1
    assert called_context.tool_name == "finish_chat_turn"
    assert result.turn_finished is True
    assert result.tags == [AgentHistoryTag.ROOM_TURN_FINISH]


@pytest.mark.asyncio
async def test_native_driver_run_chat_turn_is_disabled(driver):
    with pytest.raises(RuntimeError, match="不再直接执行 run_chat_turn"):
        await driver.run_chat_turn(room=MagicMock(), synced_count=0)
