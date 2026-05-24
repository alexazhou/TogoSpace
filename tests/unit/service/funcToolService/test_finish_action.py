from unittest.mock import AsyncMock, MagicMock
import pytest
from service.funcToolService.tools import finish_action
from service.roomService.core import ToolCallContext


def _make_context(has_content: bool) -> ToolCallContext:
    """构造一个模拟的 ToolCallContext。"""
    room = MagicMock()
    room.name = "test_room"
    room.current_turn_has_content = has_content
    room.handle_finish_request = AsyncMock(return_value=True)
    return ToolCallContext(agent_id=1, team_id=1, chat_room=room)


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
    """无聊天室上下文 → 报错。"""
    result = await finish_action(_context=None)
    assert result["success"] is False
    assert "房间上下文" in result["message"]
