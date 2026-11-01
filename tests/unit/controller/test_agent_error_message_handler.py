"""Agent 失败信息接口：校验 Agent 存在后，原样返回 service 的查询结果。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from controller import agentController
from exception import TogoException


def _patch_agent(monkeypatch, *, exists: bool = True) -> None:
    monkeypatch.setattr(
        agentController.gtAgentManager,
        "get_agents_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id=3)] if exists else []),
    )


async def test_error_message_handler_returns_service_value(monkeypatch):
    """有失败信息时返回 `{"error_message": ...}`。"""
    _patch_agent(monkeypatch)
    monkeypatch.setattr(
        agentController.agentService,
        "get_agent_error_message",
        AsyncMock(return_value="boom"),
    )

    captured: list = []
    handler = object.__new__(agentController.AgentErrorMessageHandler)
    handler.return_json = captured.append

    await handler.get("3")

    assert captured == [{"error_message": "boom"}]


async def test_error_message_handler_returns_null_without_failure(monkeypatch):
    """没有失败任务时返回 null，而不是省略字段。"""
    _patch_agent(monkeypatch)
    monkeypatch.setattr(
        agentController.agentService,
        "get_agent_error_message",
        AsyncMock(return_value=None),
    )

    captured: list = []
    handler = object.__new__(agentController.AgentErrorMessageHandler)
    handler.return_json = captured.append

    await handler.get("3")

    assert captured == [{"error_message": None}]


async def test_error_message_handler_rejects_unknown_agent(monkeypatch):
    """Agent 不存在时直接报 `agent_not_found`，不调用 service。"""
    _patch_agent(monkeypatch, exists=False)
    service = AsyncMock()
    monkeypatch.setattr(agentController.agentService, "get_agent_error_message", service)

    handler = object.__new__(agentController.AgentErrorMessageHandler)
    handler.return_json = lambda _data: None

    with pytest.raises(TogoException) as exc_info:
        await handler.get("3")

    assert exc_info.value.error_code == "agent_not_found"
    service.assert_not_called()
