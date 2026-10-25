"""#67：任务工具签名与工具面（去 `result`/`block_reason`、`new_status`、`get_task_detail`、`list_task_events`）。"""

from __future__ import annotations

import inspect

import pytest

from service.funcToolService import tools
from service.funcToolService.funcToolType import get_function_metadata


def test_update_task_signature_uses_new_status_and_note():
    """update_task 入参 = task_id / new_status / note；无 result / block_reason / status。"""
    params = list(inspect.signature(tools.update_task).parameters)
    assert params == ["task_id", "new_status", "note", "_context"]
    assert "result" not in params
    assert "block_reason" not in params
    assert "status" not in params


def test_update_task_rejects_unknown_param_result():
    """无兼容别名：传旧字段 result 直接报错（TypeError）。"""
    with pytest.raises(TypeError):
        tools.update_task(task_id=1, result="x")  # type: ignore[call-arg]


def test_update_task_rejects_unknown_param_block_reason():
    with pytest.raises(TypeError):
        tools.update_task(task_id=1, block_reason="x")  # type: ignore[call-arg]


def test_list_tasks_signature_uses_assignee_and_reviewer():
    params = list(inspect.signature(tools.list_tasks).parameters)
    assert "assignee" in params and "reviewer" in params
    assert "assignee_id" not in params and "reviewer_id" not in params


def test_list_tasks_does_not_expose_limit():
    """工具层不对外暴露 limit（走内部默认值）；服务层仍保留 limit 可选。"""
    params = list(inspect.signature(tools.list_tasks).parameters)
    assert params == ["assignee", "reviewer", "status", "open_only", "_context"]
    assert "limit" not in params

    import service.taskService as taskService

    assert "limit" in inspect.signature(taskService.list_tasks).parameters


def test_list_tasks_signature_has_open_only_default_true():
    """工具层 list_tasks 新增 open_only，默认 True（Agent 默认不看已完结）。"""
    sig = inspect.signature(tools.list_tasks)
    assert "open_only" in sig.parameters
    assert sig.parameters["open_only"].default is True


def test_list_tasks_metadata_exposes_open_only():
    """工具 schema 由 docstring 解析：properties 含 open_only（含说明），非必填，且不含 limit。"""
    meta = get_function_metadata("list_tasks", tools.list_tasks)
    props = meta["parameters"]["properties"]
    assert set(props) == {"assignee", "reviewer", "status", "open_only"}
    assert props["open_only"]["type"] == "boolean"
    assert props["open_only"].get("description")
    assert "limit" not in props
    assert meta["parameters"]["required"] == []


@pytest.mark.asyncio
async def test_list_tasks_rejects_when_no_assignee_or_reviewer():
    """assignee / reviewer 都不传 ⇒ 参数错误（missing_target），不抛异常。"""
    from service.roomService.core import ToolCallContext

    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="list_tasks")
    res = await tools.list_tasks(_context=ctx)

    assert res["success"] is False
    assert res["error_code"] == "missing_target"


@pytest.mark.asyncio
async def test_list_tasks_forwards_internal_default_limit(monkeypatch):
    """工具层固定透传内部默认 limit（不对外开放），open_only 默认 True。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return {"success": True, "tasks": [], "total": 0}

    monkeypatch.setattr(taskService, "list_tasks", _fake)
    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="list_tasks")
    res = await tools.list_tasks(assignee=8, _context=ctx)

    assert res["success"] is True
    assert captured["assignee_id"] == 8
    assert captured["open_only"] is True
    assert captured["limit"] == tools.LIST_TASKS_DEFAULT_LIMIT


def test_get_task_detail_and_deprecated_get_task_exist():
    assert callable(tools.get_task_detail)
    assert callable(tools.get_task)


def test_list_task_events_signature():
    params = list(inspect.signature(tools.list_task_events).parameters)
    assert params == ["task_id", "limit", "before_id", "_context"]


def test_tool_metadata_exposes_new_status_only():
    """工具 schema：properties 含 new_status/note、不含 result/block_reason；required 仅 task_id。"""
    meta = get_function_metadata("update_task", tools.update_task)
    props = meta["parameters"]["properties"]
    assert "new_status" in props
    assert "note" in props
    assert "result" not in props
    assert "block_reason" not in props
    assert meta["parameters"]["required"] == ["task_id"]


def test_registry_contains_new_and_deprecated_tool_names():
    """core 注册表含 get_task_detail / list_task_events，并保留废弃的 get_task。"""
    from service.funcToolService import core

    registry = core.load_func_tools()
    assert "get_task_detail" in registry
    assert "list_task_events" in registry
    assert "get_task" in registry  # 废弃别名（平滑迁移）
