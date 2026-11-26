"""#375 模块 2/4 的**真入口**判据：help 单一字段、枚举可读、hint 不再指死语法、kind 过滤。

路径与生产一致：`AgentToolRegistry.execute_tool_call` → console 解析 → 子树 →
（`ForwardToolCall`）第 2 跳 → 既有工具 → DB。**不直调** `_help_text` / `console()`：
help 的返回形态与 hint 都是模型实际读到的返回体，必须从唯一执行入口取。

对应 need_fix_4 §七 模块 2/4 的交付标准 1–5。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from container_db_schema import migrate_all
from model.dbModel.base import bind_database, unbind_database
from service import funcToolService, mcpRegistryService
from service.agentService import toolRegistry
from service.agentService.toolRegistry import AgentToolRegistry, build_runtime_allow_specs
from service.funcToolService import core as func_tool_core
from service.ormService import AioSqliteDatabase
from service.roomService import ToolCallContext
from util.llmApiUtil import OpenAIToolCall

TEAM_ID = 1


@pytest.fixture
async def entry(tmp_path: Path):
    """真库 + 真注册表（与生产 driver 同形），退出时解绑。"""
    db_path = tmp_path / "m375.db"
    migrate_all(db_path, workspace_root=tmp_path)
    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()

    registry = AgentToolRegistry()
    for name, func_tool in func_tool_core.load_func_tools().items():
        registry.register(
            func_tool.to_openai_tool(),
            funcToolService.run_tool_call,
            marks_turn_finish=name == "finish_action",
            self_interrupt=name == "reload_team",
            self_interrupt_predicate=func_tool_core.get_self_interrupt_predicate(name),
        )
    registry.apply_tool_allow_specs(build_runtime_allow_specs(None, is_root_leader=True))

    ctx = ToolCallContext(agent_id=1, team_id=TEAM_ID, chat_room=None)

    async def call(command: str) -> dict:
        tool_call = OpenAIToolCall(
            id="tc_375",
            function={"name": "console", "arguments": json.dumps({"command": command})},
        )
        result = await registry.execute_tool_call(tool_call, ctx)
        payload = result.result if hasattr(result, "result") else result
        assert isinstance(payload, dict), payload
        return payload

    try:
        yield call
    finally:
        await db.aio_close()
        unbind_database()


# --------------------------------------------------------------------------- #
# 交付标准 1：help 单一字段（三处产出点全覆盖，含 mcp/openapi/graphql 子树）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "help",
        "help task",
        "help task create",
        "help mcp",
        "help mcp list",
        "help openapi",
        "help graphql",
    ],
)
async def test_help_returns_text_only(entry, command: str) -> None:
    body = await entry(command)
    assert body["success"] is True, body
    assert set(body) == {"success", "text"}, f"{command} 的返回体还有额外字段：{sorted(body)}"
    assert body["text"].strip(), body


# --------------------------------------------------------------------------- #
# 交付标准 2：枚举不再被截断（原文作证）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_priority_enum_is_readable(entry) -> None:
    text = (await entry("help task create"))["text"]
    priority_line = next(line for line in text.splitlines() if "--priority" in line)
    for value in ("HIGH", "NORMAL", "LOW"):
        assert value in priority_line, priority_line


@pytest.mark.asyncio
async def test_new_status_all_seven_values_are_readable(entry) -> None:
    text = (await entry("help task update"))["text"]
    status_line = next(line for line in text.splitlines() if "--new-status" in line)
    for status in ("TODO", "PENDING", "IN_PROGRESS", "ON_HOLD", "REVIEWING", "DONE", "CANCELLED"):
        assert status in status_line, status_line


# --------------------------------------------------------------------------- #
# 交付标准 3：说明不再被腰斩（改前在这三处都被切在句内标点上）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_depends_on_three_states_survive(entry) -> None:
    text = (await entry("help task update"))["text"]
    line = next(line for line in text.splitlines() if "--depends-on" in line)
    # 三态说明的第三态（改前被切在 `①省略本参数 = 不改依赖`）
    assert "③" in line and "整体替换" in line, line


@pytest.mark.asyncio
async def test_stages_explanation_survives(entry) -> None:
    text = (await entry("help task create"))["text"]
    line = next(line for line in text.splitlines() if "--stages" in line)
    # 改前切在 `每项为 {"owner_id":…`
    assert "node_type" in line or "也可简写为 owner_id" in line, line


# --------------------------------------------------------------------------- #
# 交付标准 4：四条 hint 不再指向死语法
# --------------------------------------------------------------------------- #

DEAD_SYNTAX = ("mcp ls ", "mcp call <名称> --tool", "--kind")


def _assert_no_dead_syntax(text: str, *, where: str) -> None:
    for dead in DEAD_SYNTAX:
        assert dead not in text, f"{where} 仍指向死语法 {dead!r}：{text}"


@pytest.mark.asyncio
async def test_mcp_list_hint_has_no_dead_syntax(entry) -> None:
    body = await entry("mcp list")
    assert body["success"] is True, body
    hint = body.get("hint") or body.get("text") or ""
    _assert_no_dead_syntax(hint, where="mcp list 无参")
    assert "openapi" in hint and "graphql" in hint, hint


@pytest.mark.asyncio
async def test_mcp_config_list_text_has_no_dead_syntax(entry) -> None:
    body = await entry("mcp config list --output text")
    assert body["success"] is True, body
    _assert_no_dead_syntax(body.get("text") or "", where="mcp config list")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openapi", "graphql"])
async def test_spec_list_hint_is_kind_specific(entry, kind: str) -> None:
    body = await entry(f"{kind} list")
    assert body["success"] is True, body
    hint = body.get("hint") or ""
    _assert_no_dead_syntax(hint, where=f"{kind} list 无参")
    assert f"{kind} list" in hint, hint
    assert f"{kind} config add" in hint, hint


# --------------------------------------------------------------------------- #
# 交付标准 5：三棵子树的 list / config get 按 kind 对齐
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mcp_lists_only_mcp_entries(entry) -> None:
    await mcpRegistryService.save_server(name="m375-mcp", kind="mcp", target="https://x/mcp")
    await mcpRegistryService.save_server(
        name="m375-openapi", kind="openapi", target="https://x/openapi.json"
    )

    listing = await entry("mcp list")
    names = [item["name"] for item in listing["servers"]]
    assert names == ["m375-mcp"], names
    assert "openapi" in (listing.get("note") or ""), listing

    config_listing = await entry("mcp config list")
    names = [item["name"] for item in config_listing["servers"]]
    assert names == ["m375-mcp"], names


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openapi", "graphql"])
async def test_spec_lists_only_own_kind(entry, kind: str) -> None:
    await mcpRegistryService.save_server(name="m375-mcp2", kind="mcp", target="https://x/mcp")
    await mcpRegistryService.save_server(
        name="m375-graphql", kind="graphql", target="https://x/graphql"
    )

    listing = await entry(f"{kind} list")
    names = [item["name"] for item in listing["servers"]]
    assert all(name != "m375-mcp2" for name in names), names
    if kind == "openapi":
        assert "m375-graphql" not in names, names


@pytest.mark.asyncio
async def test_mcp_config_get_rejects_non_mcp_entry(entry) -> None:
    await mcpRegistryService.save_server(
        name="m375-openapi2", kind="openapi", target="https://x/openapi.json"
    )
    body = await entry("mcp config get m375-openapi2")
    assert body["success"] is False, body
    assert body["error_code"] == "mcp_kind_mismatch", body
    assert "openapi config get m375-openapi2" in body["message"], body


@pytest.mark.asyncio
async def test_spec_config_get_rejects_mcp_entry(entry) -> None:
    await mcpRegistryService.save_server(name="m375-mcp3", kind="mcp", target="https://x/mcp")
    body = await entry("openapi config get m375-mcp3")
    assert body["success"] is False, body
    assert body["error_code"] == "spec_kind_mismatch", body
    assert "mcp config get m375-mcp3" in body["message"], body
