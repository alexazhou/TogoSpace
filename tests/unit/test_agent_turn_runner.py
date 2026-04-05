"""AgentTurnRunner 单元测试：测试 Turn 执行逻辑。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryStage, AgentHistoryTag, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from model.coreModel.gtCoreChatModel import GtCoreChatMessage
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from service.roomService import ChatRoom


def _make_turn_runner() -> AgentTurnRunner:
    """构造一个最小可运行的 TurnRunner，driver 使用默认 NATIVE。"""
    gt_agent = GtAgent(id=1, team_id=1, name="TestAgent", role_template_id=1, model="mock")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        max_function_calls=5,
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )
    # 替换 _history 为 mock，避免单元测试触及数据库
    mock_history = MagicMock()
    mock_history.has_unfinished_turn = MagicMock(return_value=False)
    mock_history.append_history_message = AsyncMock()
    mock_history.export_openai_tools = MagicMock(return_value=[])
    runner._history = mock_history
    return runner


@pytest.fixture
def turn_runner():
    return _make_turn_runner()


@pytest.mark.asyncio
async def test_run_chat_turn_skips_when_room_id_missing(turn_runner):
    task = MagicMock(spec=GtAgentTask)
    task.id = 100
    task.task_data = {}  # 无 room_id

    await turn_runner.run_chat_turn(task)

    turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_chat_turn_skips_when_room_not_found(turn_runner):
    task = MagicMock(spec=GtAgentTask)
    task.id = 100
    task.task_data = {"room_id": 999}

    with patch("service.agentService.agentTurnRunner.roomService") as mock_room_service:
        mock_room_service.get_room = MagicMock(return_value=None)

        await turn_runner.run_chat_turn(task)

        turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_pull_room_messages_syncs_to_history(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.team_id = 1
    room._get_agent_name = MagicMock(return_value="OtherAgent")

    msg = MagicMock(spec=GtCoreChatMessage)
    msg.sender_id = 2  # 非 agent 自身
    msg.content = "Hello"

    room.get_unread_messages = AsyncMock(return_value=[msg])

    count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 1
    turn_runner._history.append_history_message.assert_called_once()
    call_args = turn_runner._history.append_history_message.call_args
    assert call_args[1]["stage"] == AgentHistoryStage.INPUT
    assert AgentHistoryTag.ROOM_TURN_BEGIN in call_args[1]["tags"]


@pytest.mark.asyncio
async def test_pull_room_messages_skips_own_messages(turn_runner):
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
    turn_runner._history.append_history_message.assert_not_called()


@pytest.mark.asyncio
async def test_pull_room_messages_returns_zero_when_empty(turn_runner):
    room = MagicMock(spec=ChatRoom)
    room.name = "test_room"
    room.get_unread_messages = AsyncMock(return_value=[])

    count = await turn_runner.pull_room_messages_to_history(room)

    assert count == 0
    turn_runner._history.append_history_message.assert_not_called()
