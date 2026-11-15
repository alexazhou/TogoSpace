"""MCP 注册表闭环 × **真 mcp2cli**（#224）。

`mcp add → mcp ls → mcp call → mcp rm` 全链真入口真库，call 步用**真 mcp2cli**
对本地起的 HTTP 服务实调（spec 文件指向 `http://127.0.0.1:<port>`，不出网）。

判据 1 的「实际调用成功」在这里兑现——但**只在本机 mcp2cli 可导入的环境**跑得动
（当前全量基线的 10 failed 正是「mcp2cli 缺失」这一环境事实，与本文件同源）。
标准环境（无 mcp2cli）下本文件整组 skip：skip 数量变化在 #224 报告里单独说明；
真入口闭环的**不带 mcp2cli** 版本见
`tests/unit/service/funcToolService/test_mcp_registry_console.py`
（外部包侧用 stub `_main_impl` 替身，ToGo 链路全真）。

运行方式（本机验证）::

    PYTHONPATH=src:李哥/pydeps pytest tests/integration/test_funcToolService/test_mcp_registry_real_loop.py
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("mcp2cli", reason="真 mcp2cli 实调闭环；缺失环境属基线红同源，skip")

from container_db_schema import migrate_all  # noqa: E402
from model.dbModel.base import bind_database, unbind_database  # noqa: E402
from service.agentService import toolRegistry  # noqa: E402
from service.funcToolService import core as func_tool_core  # noqa: E402
from service.funcToolService.consoleTool import console  # noqa: E402
from service.funcToolService.tools import mcp_call  # noqa: E402
from service.ormService import AioSqliteDatabase  # noqa: E402
from util import llmApiUtil  # noqa: E402


@pytest.fixture
def served_dir():
    """本地静态 JSON 服务 + 指向它的 openapi spec（与 test_mcp_call 同款，不出网）。"""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"id": "thing-1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 静默
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield None, base_url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _write_spec(directory: Path, base_url: str) -> Path:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "thing", "version": "1.0.0"},
        "servers": [{"url": base_url}],
        "paths": {
            "/thing": {
                "get": {
                    "operationId": "get-thing",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    path = directory / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


@pytest.fixture
async def mcp_db(tmp_path: Path):
    db_path = tmp_path / "mcp_registry_real.db"
    migrate_all(db_path, workspace_root=tmp_path)
    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db_path
    finally:
        await db.aio_close()
        unbind_database()


@pytest.fixture
def leader_ctx():
    return toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)


@pytest.fixture
def loaded_tools():
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()


def _build_registry() -> toolRegistry.AgentToolRegistry:
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools():
        registry.register(tool, func_tool_core.run_tool_call)
    return registry


def _console_call(call_id: str, command: str) -> llmApiUtil.OpenAIToolCall:
    return llmApiUtil.OpenAIToolCall(
        id=call_id,
        function={"name": "console", "arguments": json.dumps({"command": command})},
    )


@pytest.mark.asyncio
async def test_real_mcp2cli_closed_loop(mcp_db, leader_ctx, loaded_tools, served_dir):
    """真包闭环：add（真库）→ ls（真库）→ call（真 mcp2cli 实调本地 HTTP）→ rm（真库）。"""
    directory = Path(mcp_db).parent
    spec_path = _write_spec(directory, served_dir[1])

    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute", "Category:Admin"]
    )

    # add：登记name为 openapi spec 文件（无外部依赖的 target 形态）
    added = await registry.execute_tool_call(
        _console_call("rl_add", f"mcp add --name local --kind openapi --target {spec_path}"),
        context=leader_ctx,
    )
    assert added.result.get("success") is True, added.result

    # ls 有参：对name列工具清单（真包，list）
    listed = await registry.execute_tool_call(
        _console_call("rl_ls", "mcp ls local"), context=leader_ctx
    )
    body = listed.result
    assert body.get("success") is True, body
    assert body["resolved_from_registry"] is True
    assert body["name"] == "local"
    assert [cmd["name"] for cmd in body["result"]] == ["get-thing"]

    # call：name 解析 → 真包实调本地 HTTP（实际调用成功的判据落点）
    called = await registry.execute_tool_call(
        _console_call("rl_call", "mcp call local --tool get-thing"), context=leader_ctx
    )
    body = called.result
    assert body.get("success") is True, body
    assert body["result"] == {"id": "thing-1"}
    assert body["resolved_from_registry"] is True
    assert body["name"] == "local"

    # rm：清理并确认空表
    removed = await registry.execute_tool_call(
        _console_call("rl_rm", "mcp rm local"), context=leader_ctx
    )
    assert removed.result.get("success") is True, removed.result

    after = await registry.execute_tool_call(
        _console_call("rl_ls2", "mcp ls"), context=leader_ctx
    )
    assert after.result["count"] == 0


@pytest.mark.asyncio
async def test_real_call_without_name_unchanged(mcp_db, leader_ctx, loaded_tools, served_dir):
    """向后兼容对照：完整 target 直调（不经注册表）行为一字不改。"""
    directory = Path(mcp_db).parent
    spec_path = _write_spec(directory, served_dir[1])

    body = await mcp_call(target=str(spec_path), action="list", _context=leader_ctx)
    assert body["success"] is True, body
    assert body["resolved_from_registry"] is False
    assert "name" not in body
    assert [cmd["name"] for cmd in body["result"]] == ["get-thing"]
