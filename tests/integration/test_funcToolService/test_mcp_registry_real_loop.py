"""MCP 注册表闭环 × **真 mcp2cli**（#224）＋ console × 真 SDK 闭环（#291）。

两条闭环，各钉一条路径：

1. `test_real_mcp2cli_closed_loop`（**既有能力**，A 类）：`mcp config add --kind openapi`
   登记 spec → `mcp ls <name>` 列工具 → `mcp call <name> --tool <工具名>` 实调 →
   `mcp config remove` 清理。call 步用**真 mcp2cli** 对本地 HTTP 服务实调（不出网）。
   本测试只在 mcp2cli 可导入的环境跑（缺失即 skip，与基线红同源）。
   #291 变更只有入口写法：`mcp add --name X --kind openapi --target Y`
   → `mcp config add X Y --kind openapi`；`mcp rm X` → `mcp config remove X`。

2. `test_console_real_sdk_stdio_closed_loop`（**MCP 原生路径**，C 类新增）：console 面
   走 `mcp config add`（stdio server）→ `mcp session start` → `mcp list <name>` →
   `mcp call <name>.<tool>` → `mcp session status/stop`，全程真子进程、真 SDK。
   为什么要这一条：service 层的 lifecycle 判据不经过 `consoleTool` 的窄分支与
   `mcpConsole` 的解析/转发，只测它等于把「console 入口能不能真的调通」留空。
"""

from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

from container_db_schema import migrate_all
from model.dbModel.base import bind_database, unbind_database
from service import mcpSessionService as svc
from service.agentService import toolRegistry
from service.funcToolService import core as func_tool_core
from service.funcToolService.tools import mcp_call
from service.ormService import AioSqliteDatabase
from util import llmApiUtil


@pytest.fixture
def served_dir(tmp_path: Path):
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
        yield tmp_path, base_url
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


_STDIO_SERVER = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo_text(x: str) -> str:
    """回显"""
    return "echo:" + x


@mcp.tool()
def echo_int(x: int) -> str:
    """回显整数（用来验 schema 驱动的类型转换）"""
    return "got:" + str(x)


@mcp.resource("note://hello")
def hello() -> str:
    return "hi from resource"


mcp.run()
'''


def _stdio_command(tmp_path: Path) -> str:
    script = tmp_path / "server_echo.py"
    script.write_text(_STDIO_SERVER, encoding="utf-8")
    return f"{sys.executable} {script}"


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
        await svc.shutdown()
        svc.reset_for_tests()
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
    """真包闭环（既有 OpenAPI 能力）：add → ls（列工具）→ call（真 mcp2cli 实调）→ rm。"""
    pytest.importorskip("mcp2cli", reason="真 mcp2cli 实调闭环；缺失环境属基线红同源，skip")
    directory, base_url = served_dir
    spec_path = _write_spec(directory, base_url)

    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute", "Category:Admin"]
    )

    # add：登记 kind=openapi 的 spec 文件（既有能力：注册表本来就能存三类 kind）
    added = await registry.execute_tool_call(
        _console_call("rl_add", f"mcp config add local {spec_path} --kind openapi"),
        context=leader_ctx,
    )
    assert added.result.get("success") is True, added.result

    # ls 有参：对 name 列工具清单（真包，list）
    listed = await registry.execute_tool_call(
        _console_call("rl_ls", "mcp ls local"), context=leader_ctx
    )
    body = listed.result
    assert body.get("success") is True, body
    assert body["resolved_from_registry"] is True
    assert body["name"] == "local"
    assert [cmd["name"] for cmd in body["result"]] == ["get-thing"]

    # call：name 解析 → 真包实调本地 HTTP（实际调用成功的判据落点）。
    # 注：`mcp call <name> --tool x`（无点形态）对 kind=openapi 的注册项仍是合法既有写法。
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
        _console_call("rl_rm", "mcp config remove local"), context=leader_ctx
    )
    assert removed.result.get("success") is True, removed.result

    after = await registry.execute_tool_call(
        _console_call("rl_ls2", "mcp config list"), context=leader_ctx
    )
    assert after.result["count"] == 0


@pytest.mark.asyncio
async def test_real_call_without_name_unchanged(mcp_db, leader_ctx, loaded_tools, served_dir):
    """向后兼容对照：完整 target 直调（不经注册表）行为一字不改。"""
    pytest.importorskip("mcp2cli", reason="真 mcp2cli 实调；缺失环境 skip")
    directory, base_url = served_dir
    spec_path = _write_spec(directory, base_url)

    body = await mcp_call(target=str(spec_path), action="list", _context=leader_ctx)
    assert body["success"] is True, body
    assert body["resolved_from_registry"] is False
    assert "name" not in body
    assert [cmd["name"] for cmd in body["result"]] == ["get-thing"]


@pytest.mark.asyncio
async def test_console_real_sdk_stdio_closed_loop(mcp_db, leader_ctx, loaded_tools, tmp_path: Path):
    """console 入口 × 真 SDK 闭环（MCP 原生路径）：add → session → list → call → resource → stop。

    判据落点都在**可观察行为**上：工具清单来自真子进程、调用结果等于子进程的返回值、
    session 的连接计数随访问增减（§2.2）。
    """
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )

    async def _run(call_id: str, console_command: str) -> dict:
        result = await registry.execute_tool_call(
            _console_call(call_id, console_command), context=leader_ctx
        )
        return result.result

    added = await _run("s_add", f"mcp config add echo '{command}'")
    assert added.get("success") is True, added
    assert added["server"]["kind"] == "mcp"
    assert added["server"]["protocol"] == "stdio"

    started = await _run("s_start", "mcp session start")
    assert started["active"] is True
    assert started["connection_count"] == 0, "session start 不预建连接（§2.2）"

    listed = await _run("s_list", "mcp list echo --brief")
    assert listed["success"] is True, listed
    assert "echo_text" in listed["text"], listed["text"]

    called = await _run("s_call", "mcp call echo.echo_text x=hi")
    assert called["success"] is True, called
    assert called["text"] == "echo:hi", called

    # 参数契约（§2.1 第 2 条「参数名称/类型及错误行为按工具 schema」）三条反例：
    # 必填缺失 / 类型转换失败 / 同一参数两处来源。都必须在**真 schema** 上验，
    # 因为转换规则来自 list_tools 的 inputSchema，mock 掉就等于没验。
    missing = await _run("s_missing", "mcp call echo.echo_text")
    assert missing["error_code"] == "mcp_missing_argument", missing
    assert "x" in missing["message"], missing["message"]

    bad_type = await _run("s_badtype", "mcp call echo.echo_int x=abc")
    assert bad_type["error_code"] == "mcp_invalid_args", bad_type

    ambiguous = await _run("s_ambig", "mcp call echo.echo_text --args '{\"x\":\"a\"}' x=b")
    assert ambiguous["error_code"] == "ambiguous_input_source", ambiguous

    resource = await _run("s_res", "mcp resource echo")
    assert resource["success"] is True, resource
    assert "note://hello" in resource["text"], resource["text"]

    status = await _run("s_status", "mcp session status")
    assert status["active"] is True
    assert status["connection_count"] == 1, "同一 server 的多次访问只保留一条连接"
    assert [item["server"] for item in status["connections"]] == ["echo"]
    assert status["connections"][0]["state"] == "ready"
    assert status["connections"][0]["registered"] is True

    stopped = await _run("s_stop", "mcp session stop")
    assert stopped["active"] is False
    assert stopped["connection_count"] == 0

    # 清理：注册项删掉，下一次访问恢复一次性连接且不报错
    removed = await _run("s_rm", "mcp config remove echo")
    assert removed["success"] is True, removed
