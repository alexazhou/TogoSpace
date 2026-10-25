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
