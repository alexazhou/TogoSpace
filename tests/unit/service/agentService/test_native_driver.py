from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from model.dbModel.gtScheculeTask import GtScheculeTask
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
def mock_host() -> MagicMock:
    host = MagicMock()
    host.gt_agent = MagicMock()
    host.gt_agent.id = 1
    host.tool_registry = AgentToolRegistry()
    return host


@pytest.fixture
def driver(mock_host: MagicMock) -> NativeAgentDriver:
    config = AgentDriverConfig(driver_type="native", options={})
    return NativeAgentDriver(mock_host, config)


@pytest.mark.asyncio
async def test_native_driver_setup_registers_tools(
    driver: NativeAgentDriver,
    mock_host: MagicMock,
) -> None:
    send_tool = _make_tool("send_chat_msg")
    finish_tool = _make_tool("finish_action")
    wake_tool = _make_tool("wake_up_agent")
    read_tool = _make_tool("get_time")

    run_tool_call = AsyncMock(return_value={"success": True})
    with patch("service.funcToolService.get_tools", return_value=[send_tool, finish_tool, wake_tool, read_tool]) as get_tools, patch(
        "service.funcToolService.run_tool_call",
        run_tool_call,
    ):
        await driver.startup()
        context = ToolCallContext(
            agent_id=1,
            team_id=1,
            chat_room=MagicMock(),
        )
        result = await mock_host.tool_registry.execute_tool_call(
            llmApiUtil.OpenAIToolCall(
                id="tool_1",
                function={"name": "finish_action", "arguments": "{}"},
            ),
            context=context,
        )

    setup = driver.turn_setup

    assert setup.max_retries == 3
    assert "finish_action" in setup.hint_prompt

    exported_names = [t.function.name for t in mock_host.tool_registry.export_openai_tools()]
    assert exported_names == ["send_chat_msg", "finish_action", "wake_up_agent", "get_time"]
    get_tools.assert_called_once()

    run_tool_call.assert_called_once()
    called_args, called_context = run_tool_call.call_args.args
    assert called_args == "{}"
    assert called_context.agent_id == 1
    assert called_context.team_id == 1
    assert called_context.tool_name == "finish_action"
    assert result.success is True


@pytest.mark.asyncio
async def test_native_driver_run_chat_turn_is_disabled(driver: NativeAgentDriver) -> None:
    task = MagicMock(spec=GtScheculeTask)
    with pytest.raises(RuntimeError, match="不再直接执行 run_chat_turn"):
        await driver.run_chat_turn(task=task, synced_count=0)


@pytest.mark.asyncio
async def test_native_driver_ignores_local_tool_names_and_uses_basic_category(mock_host: MagicMock) -> None:
    send_tool = _make_tool("send_chat_msg")
    finish_tool = _make_tool("finish_action")
    wake_tool = _make_tool("wake_up_agent")
    read_tool = _make_tool("get_time")
    run_tool_call = AsyncMock(return_value={"success": True})

    driver = NativeAgentDriver(
        mock_host,
        AgentDriverConfig(driver_type="native", options={"local_tool_names": ["get_time"]}),
    )

    with patch("service.funcToolService.get_tools", return_value=[send_tool, finish_tool, wake_tool, read_tool]) as get_tools, patch(
        "service.funcToolService.run_tool_call",
        run_tool_call,
    ):
        await driver.startup()

    get_tools.assert_called_once()
    exported_names = [t.function.name for t in mock_host.tool_registry.export_openai_tools()]
    assert exported_names == ["send_chat_msg", "finish_action", "wake_up_agent", "get_time"]
