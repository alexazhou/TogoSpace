"""Agent 任务列表接口（`GET /agents/<id>/tasks.json`）：排序口径回归。

Agent 任务卡看「最近改过的在最上面」，所以这个接口必须显式传
`order=ORDER_UPDATED_DESC`——不能依赖 `taskService.list_tasks` 的优先级优先默认值
（那是团队级列表的顺序）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from controller import agentController
from dal.db import gtAgentTaskManager
from exception import TogoException


def _patch_agent(monkeypatch, *, exists: bool = True) -> None:
    monkeypatch.setattr(
        agentController.gtAgentManager,
        "get_agents_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id=3, team_id=7)] if exists else []),
    )


def _build_handler(query: dict[str, str]) -> object:
    handler = object.__new__(agentController.AgentTasksHandler)
    handler.request = SimpleNamespace(
        query_arguments={key: [value.encode()] for key, value in query.items()}
    )
    return handler


async def test_agent_tasks_handler_requests_updated_desc_order(monkeypatch):
    """**关键断言**：Agent 任务列表按「最近修改在前」请求，而不是优先级优先。"""
    _patch_agent(monkeypatch)

    received: dict = {}

    async def _list(**kwargs):
        received.update(kwargs)
        return {"success": True, "tasks": [], "count": 0, "total": 0, "limit": 30}

    monkeypatch.setattr(agentController.taskService, "list_tasks", _list)

    captured: list = []
    handler = _build_handler({})
    handler.return_json = captured.append

    await handler.get("3")

    assert received["order"] == gtAgentTaskManager.ORDER_UPDATED_DESC
    assert received["assignee_id"] == 3
    assert received["team_id"] == 7
    assert captured == [{"success": True, "tasks": [], "count": 0, "total": 0, "limit": 30}]


async def test_agent_tasks_handler_keeps_include_closed_and_limit_semantics(monkeypatch):
    """排序改动不应动到既有参数语义：`include_closed` 仍映射成 `open_only`，`limit` 仍夹在 1..100。"""
    _patch_agent(monkeypatch)

    received: dict = {}

    async def _list(**kwargs):
        received.update(kwargs)
        return {"success": True, "tasks": [], "count": 0, "total": 0, "limit": kwargs["limit"]}

    monkeypatch.setattr(agentController.taskService, "list_tasks", _list)

    handler = _build_handler({"include_closed": "1", "limit": "500"})
    handler.return_json = lambda _data: None

    await handler.get("3")

    assert received["open_only"] is False
    assert received["limit"] == 100


async def test_agent_tasks_handler_rejects_unknown_agent(monkeypatch):
    """Agent 不存在时直接报 `agent_not_found`，不触发列表查询。"""
    _patch_agent(monkeypatch, exists=False)
    service = AsyncMock()
    monkeypatch.setattr(agentController.taskService, "list_tasks", service)

    handler = _build_handler({})
    handler.return_json = lambda _data: None

    with pytest.raises(TogoException) as exc_info:
        await handler.get("3")

    assert exc_info.value.error_code == "agent_not_found"
    service.assert_not_called()
