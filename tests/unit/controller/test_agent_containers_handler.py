"""Agent 容器列表接口：校验 Agent 存在后委托 service，响应直接返回容器列表。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from controller import agentController
from exception import TogoException


async def test_containers_handler_delegates_to_service(monkeypatch):
    """成功后原样返回 service 的容器列表，controller 不再包一层。"""
    monkeypatch.setattr(
        agentController.gtAgentManager,
        "get_agents_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id=3)]),
    )
    expected = [object()]
    monkeypatch.setattr(
        agentController.agentService,
        "get_agent_containers",
        AsyncMock(return_value=expected),
    )

    captured: list = []
    handler = object.__new__(agentController.AgentContainersHandler)
    handler.return_json = captured.append

    await handler.get("3")

    assert captured == [expected]


async def test_containers_handler_rejects_unknown_agent(monkeypatch):
    """Agent 不存在时直接报 `agent_not_found`，不调用容器查询。"""
    monkeypatch.setattr(
        agentController.gtAgentManager,
        "get_agents_by_ids",
        AsyncMock(return_value=[]),
    )
    service = AsyncMock()
    monkeypatch.setattr(agentController.agentService, "get_agent_containers", service)

    handler = object.__new__(agentController.AgentContainersHandler)
    handler.return_json = lambda _data: None

    with pytest.raises(TogoException) as exc_info:
        await handler.get("3")

    assert exc_info.value.error_code == "agent_not_found"
    service.assert_not_called()
