"""AgentTurnRunner 单元测试：测试 Turn 执行逻辑。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStage, AgentHistoryTag, OpenaiLLMApiRole
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from model.coreModel.gtCoreChatModel import GtCoreChatMessage
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.roomService import ChatRoom
from util import llmApiUtil


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.gt_agent = MagicMock(spec=GtAgent)
    agent.gt_agent.id = 1
    agent.gt_agent.name = "TestAgent"
    agent.system_prompt = "You are a test agent."
    agent.max_function_calls = 5
    agent.driver = MagicMock()
    agent.driver.host_managed_turn_loop = True
    agent.driver.started = True
    agent.driver.turn_setup = MagicMock()
    agent.driver.turn_setup.max_retries = 1
    agent.driver.turn_setup.hint_prompt = ""

    agent._history = MagicMock()
    agent._history.has_unfinished_turn = MagicMock(return_value=False)
    agent._history.append_history_message = AsyncMock()
    agent._history.export_openai_tools = MagicMock(return_value=[])

    agent._tool_registry = MagicMock()
    agent._tool_registry.export_openai_tools = MagicMock(return_value=[])

    return agent


@pytest.fixture
def turn_runner(mock_agent):
    return AgentTurnRunner(mock_agent)


@pytest.mark.asyncio
async def test_run_chat_turn_skips_when_room_id_missing(turn_runner, mock_agent):
    task = MagicMock(spec=GtAgentTask)
    task.id = 100
    task.task_data = {}  # 无 room_id

    await turn_runner.run_chat_turn(task)

    mock_agent._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_chat_turn_skips_when_room_not_found(turn_runner, mock_agent):
    task = MagicMock(spec=GtAgentTask)
    task.id = 100
    task.task_data = {"room_id": 999}

    with patch("service.agentService.agentTurnRunner.roomService") as mock_room_service:
        mock_room_service.get_room = MagicMock(return_value=None)

        await turn_runner.run_chat_turn(task)

        mock_agent._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_pull_room_messages_syncs_to_history(turn_runner, mock_agent):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.team_id = 1
    room._get_agent_name = MagicMock(return_value="OtherAgent")

    msg = MagicMock(spec=GtCoreChatMessage)
    msg.sender_id = 2  # 非 agent 自身
    msg.content = "Hello"

    with patch("service.agentService.agentTurnRunner.roomService") as mock_room_service:
        with patch.object(turn_runner, "_run_chat_turn_with_host_loop", new=AsyncMock()):
            room.get_unread_messages = AsyncMock(return_value=[msg])
            mock_room_service.get_room = MagicMock(return_value=room)

            count = await turn_runner.pull_room_messages_to_history(room)

            assert count == 1
            mock_agent._history.append_history_message.assert_called_once()
            call_args = mock_agent._history.append_history_message.call_args
            assert call_args[1]["stage"] == AgentHistoryStage.INPUT
            assert AgentHistoryTag.ROOM_TURN_BEGIN in call_args[1]["tags"]


@pytest.mark.asyncio
async def test_pull_room_messages_skips_own_messages(turn_runner, mock_agent):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room._get_agent_name = MagicMock(return_value="TestAgent")

    # 自己发的消息，应跳过
    msg = MagicMock(spec=GtCoreChatMessage)
    msg.sender_id = 1  # agent.gt_agent.id
    msg.content = "My message"

    room.get_unread_messages = AsyncMock(return_value=[msg])

    count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 0
    mock_agent._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_pull_room_messages_returns_zero_when_empty(turn_runner, mock_agent):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.get_unread_messages = AsyncMock(return_value=[])

    count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 0
    mock_agent._history.append_history_message.assert_not_called()