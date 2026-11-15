"""MCP 服务后台 controller：只做参数校验 + 调 mcpRegistryService，不复制业务规则。

关键判据（#277）：Web 响应给**完整值**（target/config 原样，配置页要能回显
编辑）；脱敏由展示面（mask_config_for_display / mcpTool._mask_deep）承担，
本层不做打码也不做业务校验。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from controller import mcpController
from service import mcpRegistryService as svc


def _entry(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        name="ctx7",
        kind="mcp",
        protocol="streamable_http",
        target="https://mcp.example.com/mcp",
        note="文档检索",
        config={"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}},
        enable=1,
        created_by=0,
        updated_at="2026-11-15 20:00:00",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _post_handler(handler_cls, body: dict, monkeypatch_target=None):
    handler = object.__new__(handler_cls)
    handler.request = SimpleNamespace(body=json.dumps(body).encode("utf-8"))
    captured: list = []
    handler.return_json = captured.append
    return handler, captured


async def test_list_returns_full_values_not_masked(monkeypatch):
    """列表给**完整值**（Web 配置页要能回显编辑）；不许出现 to_json 的掩码形态。"""
    monkeypatch.setattr(svc, "list_servers", AsyncMock(return_value=[_entry()]))
    handler = object.__new__(mcpController.McpServerListHandler)
    captured: list = []
    handler.return_json = captured.append
    await handler.get()

    body = captured[0]
    assert body["count"] == 1
    server = body["servers"][0]
    assert server["target"] == "https://mcp.example.com/mcp"
    assert server["config"] == {"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}}
    assert "target_masked" not in server and "config_masked" not in server


async def test_save_delegates_to_service_with_created_by_zero(monkeypatch):
    """新增/覆盖：参数原样转发 service；created_by 固定 0（Web 无 agent 上下文）。"""
    received: dict = {}

    async def _save(**kwargs):
        received.update(kwargs)
        return _entry(note=kwargs.get("note", ""), config=kwargs.get("config") or {})

    monkeypatch.setattr(svc, "save_server", _save)
    handler, captured = _post_handler(
        mcpController.McpServerSaveHandler,
        {
            "name": "ctx7",
            "kind": "mcp",
            "target": "https://mcp.example.com/mcp",
            "note": "n",
            "config": {"headers": {"Authorization": "Bearer v"}},
            "overwrite": True,
        },
    )
    await handler.post()

    assert received == {
        "name": "ctx7",
        "kind": "mcp",
        "protocol": None,
        "target": "https://mcp.example.com/mcp",
        "note": "n",
        "config": {"headers": {"Authorization": "Bearer v"}},
        "overwrite": True,
        "created_by": 0,
    }
    assert captured[0]["config"] == {"headers": {"Authorization": "Bearer v"}}


async def test_delete_and_enable_delegate_by_name(monkeypatch):
    """删除/启停：只按 name 定位，转发 service，返回 service 结果。"""
    removed = _entry()
    monkeypatch.setattr(svc, "delete_server", AsyncMock(return_value=removed))
    handler, captured = _post_handler(mcpController.McpServerDeleteHandler, {"name": "ctx7"})
    await handler.post()
    svc.delete_server.assert_awaited_once_with("ctx7")
    assert captured[0]["deleted"]["name"] == "ctx7"

    enabled = _entry(enable=1)
    monkeypatch.setattr(svc, "set_enabled", AsyncMock(return_value=enabled))
    handler, captured = _post_handler(
        mcpController.McpServerEnableHandler, {"name": "ctx7", "enable": True}
    )
    await handler.post()
    svc.set_enabled.assert_awaited_once_with("ctx7", True)
    assert captured[0]["enable"] is True


async def test_save_invalid_body_reports_mcp_request_invalid():
    """缺必填字段 ⇒ mcp_request_invalid（HTTP 400），不进 service。"""
    handler, _captured = _post_handler(mcpController.McpServerSaveHandler, {"name": "ctx7"})
    errors: list[dict] = []
    handler.return_with_error = lambda **kwargs: errors.append(kwargs)  # type: ignore[method-assign]
    await handler.post()
    assert errors and errors[0]["error_code"] == "mcp_request_invalid"


def test_route_registers_all_four_handlers():
    """route.py 必须注册四条 MCP 路由（漏注册 = 后台整块不可用，#198 的坑）。"""
    from route import application  # noqa: PLC0415

    registered: set = set()
    for rule in application.wildcard_router.rules:
        registered.add(rule.target)
    required = {
        mcpController.McpServerListHandler,
        mcpController.McpServerSaveHandler,
        mcpController.McpServerDeleteHandler,
        mcpController.McpServerEnableHandler,
    }
    assert required <= registered, required - registered
