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
from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleParsing as cp
from service.funcToolService import mcpTool
from service.funcToolService.consoleTool import console
from service.ormService import AioSqliteDatabase
from service.funcToolService.consoleParsing import ConsoleError


# --------------------------------------------------------------------------- #
# P1 / P2：文案与错误码
# --------------------------------------------------------------------------- #


def _parse_error(command_spec: str, tokens: list[str]) -> ConsoleError:
    spec = cc.COMMAND_INDEX[command_spec]
    with pytest.raises(ConsoleError) as caught:
        cp._parse_options(spec, tokens)
    return caught.value


def test_p1_unknown_option_gives_copyable_example_not_option_list():
    """P1：`mcp call ... --library-name React` ⇒ unknown_option + 一行正确调用。

    三档判据：① 清单消失；② MCP 专属说明在；③ 示例行 == 常量 `MCP_CALL_EXAMPLE`
    （不是拼出来的）。
    """
    exc = _parse_error("mcp call", ["context7", "resolve-library-id", "--library-name", "React"])
    assert exc.code == "unknown_option"
    assert "可用选项" not in exc.message, f"清单未移除：{exc.message!r}"
    assert cc.MCP_OPTION_TIP in exc.message, f"缺 MCP 专属说明：{exc.message!r}"
    assert cc.MCP_CALL_EXAMPLE in exc.message, f"示例行不是常量：{exc.message!r}"
    assert exc.extra.get("example") == cc.MCP_CALL_EXAMPLE
    # 措辞与文档 §P1 期望一致：先「未知选项」、再说明、再「正确调用：」
    assert exc.message.startswith("未知选项：--library-name。")
    assert "正确调用：" in exc.message


def test_p2_target_as_option_reuses_p1_same_segment():
    """P2：`mcp call --target x` ⇒ **unknown_option**（不是 missing_target），复用 P1 段。

    「复用」的可执行判据：把选项名归一后，两条 message **逐字节相同**，且 `example`
    同一常量 —— 不是「文案看起来像」，而是同一实现产出的同一段。
    """
    p2 = _parse_error("mcp call", ["--target", "context7", "--tool", "x"])
    assert p2.code == "unknown_option", "P2 必须复用 P1 的错误码（性质=不被接受的选项）"
    p1 = _parse_error("mcp call", ["--library-name", "React"])
    assert p2.message.replace("--target", "--library-name") == p1.message
    assert p2.extra.get("example") == p1.extra.get("example") == cc.MCP_CALL_EXAMPLE
    assert cc.MCP_OPTION_TIP in p2.message


def test_p2_missing_target_and_ambiguous_keep_old_codes():
    """P2 的边界：没写 target ⇒ `missing_target`；位置参数 + `--target` ⇒ `ambiguous_input_source`。"""
    bare = cc.COMMAND_INDEX["mcp call"]
    # 完全没写 target：解析层不报错（arity 由路由层判），这里只确认没被 P2 特例吃掉
    positional, options, _src = cp._parse_options(bare, ["--tool", "x"])
    assert positional == [] and options == {"tool": "x"}

    # 位置参数与 --target 同时给：既有 §3.6-5 契约不变（在路由层报 ambiguous）
    positional, options, _src = cp._parse_options(bare, ["ctx7", "--target", "https://x"])
    assert positional == ["ctx7"]
    assert bare.normalize_option("target") in {bare.normalize_option(k) for k in options}


def test_p1_example_declared_once_and_shared():
    """常量单一实现取证：示例文案**只有一处定义**，P1/P2 走同一构造点。

    量具（两条，互相独立）：
    1. `MCP_CALL_EXAMPLE` 的**定义行**在 `src/service/funcToolService/*.py` 里恰好 1 条
       （`MCP_CALL_EXAMPLE: str = ...`）；docstring 举例、别的命令自己的示例文案不算定义；
    2. `consoleParsing._unknown_option_error` 是本文件里**唯一**构造未知选项错误的地方：
       源码中它的出现次数 = 1 次定义 + 2 次调用（P1 未知选项 / P2 位置参数写成选项）。
    第二份示例常量、或 P2 另写一份文案，这两条都会红 —— 正是 P1「两处文案各自漂移」要防的。
    """
    import re

    root = Path(__file__).resolve().parents[4] / "src/service/funcToolService"
    definitions = [
        f"{path.name}:{idx}"
        for path in sorted(root.glob("*.py"))
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"^MCP_CALL_EXAMPLE\s*:", line)
    ]
    assert definitions == ["consoleCommands.py:479"], f"示例常量定义行异常：{definitions}"

    parsing_src = (
        Path(__file__).resolve().parents[4] / "src/service/funcToolService/consoleParsing.py"
    ).read_text(encoding="utf-8")
    assert parsing_src.count("_unknown_option_error") == 3, (
        "未知选项错误应只有一处构造点（1 定义 + 2 调用：P1 未知选项 / P2 位置参数写成选项）"
    )


def test_display_options_excludes_positional_params():
    """P1 第 2 条：位置参数不进「选项面」展示清单（`mcp call` 的 target、`mcp rm` 的 name）。"""
    call = cc.COMMAND_INDEX["mcp call"]
    assert "target" in call.positional_param_names
    assert "target" not in call.display_options, "位置参数不该出现在选项展示清单里"
    assert {"tool", "args", "kind", "extra"} <= set(call.display_options)
    rm = cc.COMMAND_INDEX["mcp rm"]
    assert "name" in rm.positional_param_names
    assert "name" not in rm.display_options


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
async def test_p4_url_openapi_failure_suggests_kind_mcp(mcp_db, leader_ctx, monkeypatch):
    """P4：默认 openapi + http(s) URL + 失败 ⇒ message 追加 `--kind mcp` 两步示例。

    判据三档：① 提示出现；② `error_code` **原样**（`mcp2cli_error`，不新增码）；
    ③ 原 message 仍在（只追加，不覆盖）。
    """
    from service.funcToolService import tools

    async def _fail(**_kwargs):
        return {"success": False, "error_code": "mcp2cli_error", "message": "原始错误：405"}

    monkeypatch.setattr(mcpTool, "call_mcp", _fail)
    body = await tools.mcp_call(target="https://mcp.context7.com/mcp", tool="x")
    assert body["success"] is False
    assert body["error_code"] == "mcp2cli_error", "P4 不许新增/改写错误码"
    assert body["message"].startswith("原始错误：405")
    assert "若目标是 MCP server，请加 --kind mcp：" in body["message"]
    assert "mcp call <URL> --kind mcp --tool <工具名>" in body["message"]


@pytest.mark.asyncio
async def test_p4_no_suggestion_when_kind_explicit_or_not_url(mcp_db, leader_ctx, monkeypatch):
    """P4 反向：显式给了 `--kind`、或 target 不是 http(s) URL ⇒ 不加提示（不制造噪音）。"""
    from service.funcToolService import tools

    async def _fail(**_kwargs):
        return {"success": False, "error_code": "mcp2cli_error", "message": "boom"}

    monkeypatch.setattr(mcpTool, "call_mcp", _fail)
    explicit = await tools.mcp_call(target="https://mcp.context7.com/mcp", tool="x", kind="mcp")
    assert "若目标是 MCP server" not in explicit["message"]
    local = await tools.mcp_call(target="./openapi.json", tool="x")
    assert "若目标是 MCP server" not in local["message"]


# --------------------------------------------------------------------------- #
# 污染判据：非 mcp 命令不带 MCP 文案、也不带清单
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command_spec", ["task ls", "dept get", "agent wake", "room rm"])
def test_non_mcp_unknown_option_not_polluted(command_spec: str):
    """P1 的硬边界：`unknown_option` 是通用码，非 mcp 命令不得弹出 MCP 文案。"""
    spec = cc.COMMAND_INDEX[command_spec]
    assert spec.example == "", f"{command_spec} 不该声明示例（示例只挂 mcp call）"
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
            kind="mcp", target="https://x/mcp", action="list",
            tool=None, args=None, extra={key: True},
        )
    assert caught.value.error_code == "mcp_unsupported_extra"
    assert "不支持 session 开关" in caught.value.message
    assert "Python 解释器" in caught.value.message, "必须给原因（打包后无解释器）"


def test_session_extra_false_or_none_is_ignored():
    """反向：`session: False` / `None` 等于「没用」，不报错（沿用 extra 的既有语义）。"""
    from service.funcToolService import mcpTool

    argv = mcpTool.build_argv(
        kind="mcp", target="https://x/mcp", action="list",
        tool=None, args=None, extra={"session": False, "session-start": None},
    )
    assert "--session" not in argv


def test_session_unsupported_documented_in_help_and_tool_docstrings():
    """「标明」硬要求：help（mcp call / mcp ls）与工具 docstring 都要写明四个开关名 + 原因。

    四处覆盖：`consoleTool.HELP_DETAIL['mcp call']` / `['mcp ls']` /
    `tools.mcp_call.__doc__` / `tools.list_mcp_servers.__doc__`。
    """
    from service.funcToolService import consoleTool, tools

    texts = {
        "HELP_DETAIL mcp call": consoleTool.HELP_DETAIL["mcp call"],
        "HELP_DETAIL mcp ls": consoleTool.HELP_DETAIL["mcp ls"],
        "mcp_call docstring": tools.mcp_call.__doc__ or "",
        "list_mcp_servers docstring": tools.list_mcp_servers.__doc__ or "",
    }
    for label, text in texts.items():
        for flag in ("--session", "--session-start", "--session-stop", "--session-list"):
            assert flag in text, f"{label} 未标明不支持 {flag}"
    # 原因必须写出来（否则使用者只看到「不支持」会反复试）
    assert "Python 解释器" in texts["HELP_DETAIL mcp call"]
    assert "Python 解释器" in texts["mcp_call docstring"]
