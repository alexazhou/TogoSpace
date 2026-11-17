"""Agent 任务列表接口（`GET /agents/<id>/tasks.json`）：排序口径回归。

Agent 任务卡看「最近改过的在最上面」，所以这个接口必须显式传
`order=ORDER_UPDATED_DESC`——不能依赖 `taskService.list_tasks` 的优先级优先默认值
（那是团队级列表的顺序）。
"""

from __future__ import annotations

import inspect
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
    assert received["caller_id"] == 3
    assert received["mine"] is True
    # 未完成视图不带范围过滤：#303 把「不传 scope」改成「显式传 scope=None」，语义等价。
    assert received["scope"] is None
    assert "assignee_id" not in received
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
    assert received["caller_id"] == 3
    assert received["scope"] == "self"
    # 原断言是 `"mine" not in received`（断言「不传这个键」= 调用形态）。#303 为了
    # 让类型可精化，改成显式传默认值，形态必然变；保护对象改为「值等于签名默认值」，
    # 语义等价（`list_tasks(mine: bool = False)`），互斥性仍由「另一个过滤器取默认值」保证。
    assert received["mine"] is False
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


@pytest.mark.parametrize(
    ("query", "pre_fix_filter"),
    [
        ({}, {"mine": True}),
        ({"include_closed": "1"}, {"scope": "self"}),
    ],
)
async def test_agent_tasks_handler_filter_args_equal_pre_fix_semantics(
    monkeypatch, query: dict[str, str], pre_fix_filter: dict
):
    """#303 的类型修复不得改变运行语义（两个分支都要钉住）。

    判据不是「键集合相同」——修复前是 `**filter_kwargs` 只传一个过滤器，修复后
    显式传两个具名 kwarg，键集合必然不同。这里改成**按 `list_tasks` 的真实签名绑定
    并补默认值**，再与修复前那种调用形态（`pre_fix_filter`）逐参数比较：相等即说明
    交给服务层的有效参数与 `2ad09c52` 完全一致，包括 `include_closed` 的两种取值。
    """
    _patch_agent(monkeypatch)

    # 必须在替换成捕获函数之前取签名，否则拿到的是 `_list(**kwargs)` 的签名。
    signature = inspect.signature(agentController.taskService.list_tasks)

    received: dict = {}

    async def _list(**kwargs):
        received.update(kwargs)
        return {"success": True, "tasks": [], "count": 0, "total": 0, "limit": 30}

    monkeypatch.setattr(agentController.taskService, "list_tasks", _list)

    handler = _build_handler(query)
    handler.return_json = lambda _data: None

    await handler.get("3")

    actual = signature.bind(**received)
    actual.apply_defaults()

    expected = signature.bind(
        team_id=7,
        caller_id=3,
        open_only="include_closed" not in query,
        limit=30,
        order=gtAgentTaskManager.ORDER_UPDATED_DESC,
        **pre_fix_filter,
    )
    expected.apply_defaults()

    assert dict(actual.arguments) == dict(expected.arguments)
