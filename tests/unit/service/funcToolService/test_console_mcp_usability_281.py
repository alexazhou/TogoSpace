"""#281 console 报错/提示文案改造判据（P1–P5，Operator 2026-11-16 02:14 批准）。

六组判据，对应单里 6 条实收：

1. **P1** 未知选项不再列「可用选项」清单，改为一行可复制的正确调用（`mcp call`）；
2. **P2** `mcp call --target x`（选项形态错）**复用 P1 同一段**：同一错误码
   `unknown_option`、同一示例常量、不新增错误码；`missing_target` / `ambiguous_input_source`
   两个既有口径不变；
3. **P3** `mcp ls` 无参给固定 hint（一段覆盖有/无配置）；
4. **P4** 直接传 URL 且按默认 `openapi` 解析失败时，提示加 `--kind mcp`（不改 error_code）；
5. **P5** `mcp ls <名称>` 成功体给一行固定 `mcp call` 模板；
6. **不污染**：非 mcp 命令（`task ls` / `dept get`）的未知选项提示里，**不得出现**
   MCP 专属文案、也不得出现「可用选项」清单。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from container_db_schema import migrate_all
from model.dbModel.base import bind_database, unbind_database
from service import mcpRegistryService  # noqa: F401  (确保模块可导入)
from service.agentService import toolRegistry
from service.funcToolService import consoleRegistry
from service.funcToolService import consoleParsing as cp
from service.funcToolService import mcpTool
from service.funcToolService.consoleTool import console
from service.ormService import AioSqliteDatabase
from service.funcToolService.consoleParsing import ConsoleError


# --------------------------------------------------------------------------- #
# P1 / P2：文案与错误码
# --------------------------------------------------------------------------- #


def _parse_error(command_spec: str, tokens: list[str]) -> ConsoleError:
    """通用命令表的解析层（**只对非 mcp 命令有效**：#291 起 mcp 子树不走 spec）。"""
    spec = consoleRegistry.get_table().by_path[command_spec]
    with pytest.raises(ConsoleError) as caught:
        cp._parse_options(spec, tokens)
    return caught.value


async def _mcp_error(command: str) -> dict:
    """mcp 子树的失败返回体（子树自己接住 ConsoleError，不抛）。"""
    body = await console(command)
    assert body.get("success") is False, body
    return body


@pytest.mark.asyncio
async def test_p1_unknown_option_gives_copyable_example_not_option_list():
    """P1：未知选项 ⇒ `unknown_option` + 一行正确调用，**不列「可用选项」清单**。

    #291：判据对象从 spec 解析层换到 mcp 子树（`mcpConsole._reject_unknown_options`），
    三档不动：① 清单消失；② 有原因说明；③ 示例行 == 唯一常量 `mcpConsole.EX_CALL`。
    """
    from service.funcToolService import consoleMcp as mcp_console

    body = await _mcp_error("mcp call ctx7.resolve-library-id --library-name React")
    assert body["error_code"] == "unknown_option", body
    message = body["message"]
    assert "可用选项" not in message, f"清单未移除：{message!r}"
    assert "未知选项：--library-name" in message, message
    assert "正确调用：" in message, message
    assert body["example"] == mcp_console.EX_CALL, body


@pytest.mark.asyncio
async def test_p2_target_option_is_legacy_form_and_conflict_still_ambiguous():
    """`openapi call --target` 仍可转发 `openapi_call`；位置参数与 `--target` 同时给 ⇒ `ambiguous_input_source`。"""
    from exception import ForwardToolCall

    with pytest.raises(ForwardToolCall) as caught:
        await console("openapi call --target https://x/openapi.json --tool t")
    forward = caught.value
    assert forward.tool_name == "openapi_call"
    assert forward.tool_args["target"] == "https://x/openapi.json"

    body = await _mcp_error("openapi call ctx7 --target https://x/openapi.json")
    assert body["error_code"] == "ambiguous_input_source", body


@pytest.mark.asyncio
async def test_p2_missing_target_keeps_explicit_code():
    """P2 的边界：完全没写 target ⇒ 明确报缺（`mcp_missing_target`），且给可抄示例。"""
    body = await _mcp_error("mcp call")
    assert body["error_code"] == "mcp_missing_target", body
    assert body["example"], body


@pytest.mark.asyncio
async def test_p1_example_declared_once_and_shared():
    """常量单一实现取证：示例文案**只有一处定义**，错误体与 help 共用它。

    量具（两条，互相独立）：
    1. `EX_CALL` 的**定义行**（`EX_CALL = …` 或 `EX_CALL: str = …`）在
       `src/service/funcToolService/*.py` 里恰好 1 条
       （#291 起位于 `mcpConsole.py`；原来的 `MCP_CALL_EXAMPLE` 随 mcp spec 一起删除）；
    2. `consoleParsing._unknown_option_error` 仍是通用路径里**唯一**构造未知选项错误的地方
       （1 定义 + 2 调用）—— mcp 子树有自己的构造点，但不能把通用路径也拆成两份。
    """
    import re

    root = Path(__file__).resolve().parents[4] / "src/service/funcToolService"
    definitions = [
        f"{path.name}:{idx}"
        for path in sorted(root.glob("*.py"))
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"^EX_CALL\s*(?::|=)", line)
    ]
    # 只断言「哪一个文件、几条定义」，**不绑行号**：行号会随无关改动漂移
    assert [entry.split(":")[0] for entry in definitions] == ["consoleMcp.py"], (
        f"示例常量应只在 consoleMcp.py 定义一次，实得：{definitions}"
    )

    parsing_src = (
        Path(__file__).resolve().parents[4] / "src/service/funcToolService/consoleParsing.py"
    ).read_text(encoding="utf-8")
    assert parsing_src.count("_unknown_option_error") == 3, (
        "通用路径的未知选项错误应只有一处构造点（1 定义 + 2 调用）"
    )


@pytest.mark.asyncio
async def test_positional_target_never_shows_up_as_option():
    """P1 第 2 条：位置参数不进「选项面」。

    原判据读 spec 的 `display_options`；#291 起 mcp 子树没有「可用选项」清单（P1 的
    目标就是取消清单），所以改钉**解析结果**：`<server>.<tool>` 归位置参数，
    `--args/--tool/--kind/--extra` 归选项，且错误体里不出现 `--<位置参数名>` 形态的清单行。
    """
    from service.funcToolService import consoleMcp as mcp_console

    positional, options, named = mcp_console.parse_tokens(
        ["call", "ctx7.get_issue", "--args", '{"id":"7"}', "--tool", "t"]
    )
    assert positional == ["call", "ctx7.get_issue"]
    assert options == {"args": '{"id":"7"}', "tool": "t"}
    assert named == {}

    body = await _mcp_error("mcp call ctx7.get_issue --bogus 1")
    assert "可用选项" not in body["message"], body["message"]


@pytest.mark.asyncio
async def test_f1_kind_default_is_gone_and_direct_pass_requires_explicit_kind():
    """`mcp` 命令不再混合 openapi/graphql，也不再出现 `--kind` 歧义。"""
    from service.funcToolService import consoleMcp as mcp_console

    body = await _mcp_error("mcp call")
    assert body["error_code"] == "mcp_missing_target", body
    assert "--kind" not in body["example"], body["example"]

    call_help = mcp_console.MCP_HELP_DETAIL["call"]
    assert "openapi call" in call_help and "graphql call" in call_help, call_help

    for text in (mcp_console.MCP_HELP_SUMMARY, call_help):
        assert "（默认）" not in text, text


# --------------------------------------------------------------------------- #
# P3 / P5：hint（真库 + 真入口）
# --------------------------------------------------------------------------- #


@pytest.fixture
async def mcp_db(tmp_path: Path):
    db_path = tmp_path / "m281.db"
    applied = migrate_all(db_path, workspace_root=tmp_path)
    assert "0023_add_mcp_servers.sql" in applied
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


async def _console_error(command: str) -> dict:
    from exception import ForwardToolCall

    try:
        await console(command)
    except ForwardToolCall as forward:
        return {"forward": forward}
    raise AssertionError(f"{command!r} 未按预期抛错")


@pytest.mark.asyncio
async def test_p3_ls_without_arg_has_fixed_hint(mcp_db, leader_ctx):
    """P3：`mcp ls` 无参 ⇒ 固定 hint（**有/无配置同一段**，不拼真实名称）。"""
    from service.funcToolService.tools import list_mcp_servers

    # 空注册表：hint 必须在
    empty = await list_mcp_servers(_context=leader_ctx)
    assert empty["count"] == 0
    assert empty["hint"] == mcpTool_ls_hint()

    # 有 1 条登记：hint 逐字节相同（证明零状态依赖）
    await mcpRegistryService.save_server(
        name="ctx7", kind="mcp", target="https://mcp.context7.com/mcp"
    )
    filled = await list_mcp_servers(_context=leader_ctx)
    assert filled["count"] == 1
    assert filled["hint"] == empty["hint"], "hint 不应随注册表状态变化（§0.1 理由②）"


def mcpTool_ls_hint() -> str:
    from service.funcToolService import tools

    return tools.MCP_LS_HINT


@pytest.mark.asyncio
async def test_p5_ls_with_name_has_call_template(mcp_db, leader_ctx, monkeypatch):
    """P5：`mcp ls <名称>` 成功体补一行固定 `mcp call` 模板（`--tool` 取 name 字段）。"""
    from service.funcToolService import tools

    await mcpRegistryService.save_server(
        name="ctx7", kind="mcp", target="https://mcp.context7.com/mcp"
    )

    async def _fake_call_mcp(**_kwargs):
        return {"success": True, "result": [], "result_type": "array", "truncated": False}

    monkeypatch.setattr(mcpTool, "call_mcp", _fake_call_mcp)
    body = await tools.list_mcp_servers(name_or_target="ctx7", _context=leader_ctx)
    assert body["success"] is True
    assert body["hint"] == tools.MCP_LS_CALL_HINT
    assert "--tool <工具名>" in body["hint"] and '"参数名":"值"' in body["hint"]


@pytest.mark.asyncio
async def test_p5_hint_absent_when_list_failed(mcp_db, leader_ctx, monkeypatch):
    """P5 反向：列工具失败时**不加** hint（失败已有自己的指路，别叠两句）。"""
    from service.funcToolService import tools

    async def _fail(**_kwargs):
        return {"success": False, "error_code": "mcp2cli_error", "message": "boom"}

    monkeypatch.setattr(mcpTool, "call_mcp", _fail)
    body = await tools.list_mcp_servers(name_or_target="https://x/mcp", _context=leader_ctx)
    assert body["success"] is False
    assert "hint" not in body


# --------------------------------------------------------------------------- #
# P4：kind 提示
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_p4_url_openapi_failure_suggests_mcp_native_path(mcp_db, leader_ctx, monkeypatch):
    """P4：默认 openapi + http(s) URL + 失败 ⇒ message 追加「这是 MCP server 该怎么走」。

    判据三档（与改前同）：① 提示出现；② `error_code` **原样**（`mcp2cli_error`，不新增码）；
    ③ 原 message 仍在（只追加，不覆盖）。
    #291 变更只在示例内容：旧示例 `mcp call <URL> --kind mcp --tool …` 随 T0 移除，
    改指 MCP 原生两条路（临时看：`mcp list <URL> --brief`；登记后：config add → call <name>.<tool>）。
    """
    from service.funcToolService import tools

    async def _fail(**_kwargs):
        return {"success": False, "error_code": "mcp2cli_error", "message": "原始错误：405"}

    monkeypatch.setattr(mcpTool, "call_mcp", _fail)
    body = await tools.openapi_call(target="https://mcp.context7.com/mcp", tool="x")
    assert body["success"] is False
    assert body["error_code"] == "mcp2cli_error", "P4 不许新增/改写错误码"
    assert body["message"].startswith("原始错误：405")
    assert "若目标是 MCP server" in body["message"]
    assert "mcp list <URL> --brief" in body["message"]
    assert "mcp config add <server-name> <URL>" in body["message"]
    assert "mcp call <server-name>.<工具名>" in body["message"]


@pytest.mark.asyncio
async def test_p4_no_suggestion_when_kind_explicit_or_not_url(mcp_db, leader_ctx, monkeypatch):
    """P4 反向：显式给了 `--kind`、或 target 不是 http(s) URL ⇒ 不加提示（不制造噪音）。"""
    from service.funcToolService import tools

    async def _fail(**_kwargs):
        return {"success": False, "error_code": "mcp2cli_error", "message": "boom"}

    monkeypatch.setattr(mcpTool, "call_mcp", _fail)
    explicit = await tools.graphql_call(target="https://mcp.context7.com/graphql", tool="x")
    assert "若目标是 MCP server" not in explicit["message"]
    local = await tools.openapi_call(target="./openapi.json", tool="x")
    assert "若目标是 MCP server" not in local["message"]


# --------------------------------------------------------------------------- #
# 污染判据：非 mcp 命令不带 MCP 文案、也不带清单
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command_spec", ["task ls", "dept get", "agent wake", "room rm"])
def test_non_mcp_unknown_option_not_polluted(command_spec: str):
    """P1 的硬边界：`unknown_option` 是通用码，非 mcp 命令不得弹出 MCP 文案。"""
    spec = consoleRegistry.get_table().by_path[command_spec]
    exc = _parse_error(command_spec, ["--bogus", "x"])
    assert exc.code == "unknown_option"
    assert "MCP" not in exc.message, f"{command_spec} 被 MCP 文案污染：{exc.message!r}"
    assert "可用选项" not in exc.message, f"{command_spec} 仍在列清单：{exc.message!r}"
    assert exc.message == "未知选项：--bogus。", f"{command_spec} 文案形态不符：{exc.message!r}"


# --------------------------------------------------------------------------- #
# Operator 02:27 追加：session 一族「明文不支持」+ 传入即明确拒
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key",
    ["session", "session-start", "session-stop", "session-list",
     "session_start", "session_stop", "session_list"],
)
def test_session_extra_rejected_with_reason(key: str):
    """传 session 开关 ⇒ `mcp_unsupported_extra`（明确拒），不落到 mcp2cli 的 bad_json 误报。

    码值复用 `mcp_unsupported_extra`：语义就是「这个 extra 开关在本工具里不生效」，
    与既有的 `raw`/`toon`/`head` 同一类，**不新增错误码**。
    """
    from service.funcToolService import mcpTool

    with pytest.raises(mcpTool.McpCallError) as caught:
        mcpTool.build_argv(
            kind="openapi", target="https://x/openapi.json", action="list",
            tool=None, args=None, extra={key: True},
        )
    assert caught.value.error_code == "mcp_unsupported_extra"
    assert "不支持 session 开关" in caught.value.message
    assert "Python 解释器" in caught.value.message, "必须给原因（打包后无解释器）"


def test_session_extra_false_or_none_is_ignored():
    """反向：`session: False` / `None` 等于「没用」，不报错（沿用 extra 的既有语义）。"""
    from service.funcToolService import mcpTool

    argv = mcpTool.build_argv(
        kind="openapi", target="https://x/openapi.json", action="list",
        tool=None, args=None, extra={"session": False, "session-start": None},
    )
    assert "--session" not in argv


def test_session_unsupported_documented_in_help_and_tool_docstrings():
    """原生 `mcp session` 在 help 里明写支持；`list_mcp_servers` 标明不支持 mcp2cli session 开关。"""
    from service.funcToolService import consoleMcp as mcp_console
    from service.funcToolService import tools

    list_doc = tools.list_mcp_servers.__doc__ or ""
    for flag in ("--session", "--session-start", "--session-stop", "--session-list"):
        assert flag in list_doc, f"list_mcp_servers docstring 未标明不支持 {flag}"
    assert "Python 解释器" in list_doc

    session_help = mcp_console.MCP_HELP_DETAIL["session"]
    assert "mcp session start" in session_help and "本项目扩展" in session_help, session_help
    assert "mcp session start" in mcpTool._SESSION_EXTRA_REASON, mcpTool._SESSION_EXTRA_REASON
