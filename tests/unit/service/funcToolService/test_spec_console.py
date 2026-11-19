"""`openapi` / `graphql` 独立 console 命令及 `CONSOLE_ONLY` 权限门控测试。"""
from __future__ import annotations

import json

import pytest

from exception import ForwardToolCall
from service.agentService import toolRegistry
from service.funcToolService import core as func_tool_core
from service.funcToolService import consoleMcp as mcp_console
from service.funcToolService.consoleTool import console
from util import llmApiUtil


def _build_registry() -> toolRegistry.AgentToolRegistry:
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools():
        registry.register(tool, func_tool_core.run_tool_call)
    return registry


def test_openapi_and_graphql_call_are_console_only():
    registry = _build_registry()
    exposed = {tool.function.name for tool in registry.export_openai_tools()}
    assert "mcp_call" not in exposed
    assert "openapi_call" not in exposed
    assert "graphql_call" not in exposed
    assert registry.get_registered_tool("openapi_call") is not None
    assert registry.get_registered_tool("graphql_call") is not None


@pytest.mark.asyncio
async def test_direct_call_to_openapi_or_graphql_tool_is_rejected():
    registry = _build_registry()
    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)

    for tool_name in ("openapi_call", "graphql_call"):
        call = llmApiUtil.OpenAIToolCall(
            id=f"tc_{tool_name}",
            function={"name": tool_name, "arguments": json.dumps({"target": "./x.json"})},
        )
        res = await registry.execute_tool_call(call, context=ctx)
        assert res.success is False
        assert "console" in res.result["message"]


@pytest.mark.asyncio
async def test_readonly_agent_cannot_bypass_execute_gate_via_openapi_or_graphql():
    registry = _build_registry()
    registry.apply_tool_allow_specs(["Category:Basic", "Category:Read"])
    ctx = toolRegistry.ToolCallContext(agent_id=2, team_id=1, chat_room=None)

    for cmd in (
        "openapi call https://api.example.com/openapi.json --tool getPets",
        "graphql call https://api.example.com/graphql --tool getUser",
    ):
        call = llmApiUtil.OpenAIToolCall(
            id="tc_ro",
            function={"name": "console", "arguments": json.dumps({"command": cmd})},
        )
        res = await registry.execute_tool_call(call, context=ctx)
        assert res.success is False
        assert "无权限" in res.result["message"]


@pytest.mark.asyncio
async def test_spec_console_routing_and_help():
    oapi_help = await console("openapi --help")
    assert oapi_help["success"] is True
    assert "openapi call" in oapi_help["text"]

    gql_help = await console("graphql --help")
    assert gql_help["success"] is True
    assert "graphql call" in gql_help["text"]

    with pytest.raises(ForwardToolCall) as gql_call:
        await console("graphql call https://gql.example.com --tool viewer")
    assert gql_call.value.tool_name == "graphql_call"
    assert gql_call.value.tool_args["action"] == "call"
    assert gql_call.value.tool_args["tool"] == "viewer"

    with pytest.raises(ForwardToolCall) as oapi_add:
        await console("openapi config add pet https://pet.example.com/openapi.json")
    assert oapi_add.value.tool_name == "save_mcp_server"
    assert oapi_add.value.tool_args["kind"] == "openapi"


@pytest.mark.asyncio
async def test_mcp_session_help_and_start_message():
    assert "跨调用保留" in mcp_console.MCP_HELP_SUMMARY
    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)
    res = await console("mcp session start", _context=ctx)
    assert res["active"] is True
    assert "跨调用保留" in res["message"]
    assert "mcp call" in res["message"]
    await console("mcp session stop", _context=ctx)
