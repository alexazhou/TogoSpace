from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from constants import AgentTaskType, TaskStatus
from service.funcToolService.tools import finish_action
from service.roomService.core import ToolCallContext


def _make_context(has_content: bool) -> ToolCallContext:
    """构造一个模拟的 ToolCallContext。"""
    room = MagicMock()
    room.name = "test_room"
    room.current_turn_has_content = has_content
    room.handle_finish_request = AsyncMock(return_value=True)
    return ToolCallContext(agent_id=1, team_id=1, chat_room=room)


def _make_collab_context(agent_task_status: TaskStatus = TaskStatus.DONE) -> ToolCallContext:
    """构造协作任务模式的 ToolCallContext（含 schedule_task 和 mock agent_task）。"""
    schedule_task = MagicMock()
    schedule_task.task_type = AgentTaskType.TODO_TASK
    schedule_task.task_data = {"agent_task_id": 42}
    agent_task = MagicMock()
    agent_task.title = "测试任务"
    agent_task.status = agent_task_status
    return ToolCallContext(agent_id=1, team_id=1, chat_room=None, schedule_task=schedule_task), agent_task


@pytest.mark.asyncio
async def test_finish_normal_has_content() -> None:
    """已发言，不带参数 → 成功。"""
    ctx = _make_context(has_content=True)
    result = await finish_action(_context=ctx)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_finish_no_content_with_confirm() -> None:
    """未发言，confirm_no_need_talk=true → 成功（跳过）。"""
    ctx = _make_context(has_content=False)
    result = await finish_action(_context=ctx, confirm_no_need_talk=True)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_finish_no_content_without_confirm() -> None:
    """未发言，不带参数 → 报错，给出分步指引。"""
    ctx = _make_context(has_content=False)
    result = await finish_action(_context=ctx)
    assert result["success"] is False
    assert "finish 失败" in result["message"]
    assert "未在收到消息的房间" in result["message"]
    assert "send_chat_msg" in result["message"]
    assert "confirm_no_need_talk=true" in result["message"]


@pytest.mark.asyncio
async def test_finish_has_content_with_confirm() -> None:
    """已发言，confirm_no_need_talk=true → 报错，阻止惯性使用。"""
    ctx = _make_context(has_content=True)
    result = await finish_action(_context=ctx, confirm_no_need_talk=True)
    assert result["success"] is False
    assert "已经在房间发言了" in result["message"]
    assert "confirm_no_need_talk" in result["message"]


@pytest.mark.asyncio
async def test_finish_no_context() -> None:
    """无上下文 → 报错。"""
    result = await finish_action(_context=None)
    assert result["success"] is False
    assert "上下文" in result["message"]


@pytest.mark.asyncio
async def test_finish_collaboration_task_no_room() -> None:
    """协作任务模式：任务已更新（非 TODO）→ finish 成功。"""
    ctx, mock_task = _make_collab_context(TaskStatus.DONE)
    with patch("service.funcToolService.tools.gtAgentTaskManager.get_task", AsyncMock(return_value=mock_task)):
        result = await finish_action(_context=ctx)
    assert result["success"] is True
    assert "已结束了本轮行动" in result["message"]


@pytest.mark.asyncio
async def test_finish_collaboration_task_still_todo() -> None:
    """协作任务模式：任务仍为 TODO → finish 失败，提示先更新任务（含 ON_HOLD 选项）。"""
    ctx, mock_task = _make_collab_context(TaskStatus.TODO)
    with patch("service.funcToolService.tools.gtAgentTaskManager.get_task", AsyncMock(return_value=mock_task)):
        result = await finish_action(_context=ctx)
    assert result["success"] is False
    assert "TODO" in result["message"]
    assert "update_task" in result["message"]
    assert "ON_HOLD" in result["message"]
