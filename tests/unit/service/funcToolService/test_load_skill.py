"""Unit tests for load_skill tool function — only tests that do NOT require database access."""
import pytest

from service.funcToolService.tools import load_skill
from service.roomService.core import ToolCallContext
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_load_skill_no_context():
    """无上下文时返回错误。"""
    result = await load_skill(skill_name="code_review", _context=None)
    assert result["success"] is False
    assert "无法获取当前 Agent 上下文" in result["message"]


@pytest.mark.asyncio
async def test_load_skill_no_agent_id():
    """_context.agent_id 为空时返回错误。"""
    ctx = ToolCallContext(agent_id=None, team_id=1, chat_room=MagicMock())
    result = await load_skill(skill_name="code_review", _context=ctx)
    assert result["success"] is False
    assert "无法获取当前 Agent 上下文" in result["message"]