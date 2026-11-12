"""`mcp_call`（#202 mcp2cli 接入）的判据。

三类判据分开，避免「跑绿了但没测到东西」：
1. **纯函数**：`build_argv` 拼出来的 argv 是不是 mcp2cli 认的形态（不起网络）；
2. **实跑**：`action=list` 读本地 spec、`action=call` 真发一次 HTTP（本地起的静态服务，
   不出网），错误路径（spec 不存在）必须返回结构化失败而**不是把进程带走**；
3. **接入面**：`console mcp ...` 必须转发到 `mcp_call`，并且走 `execute_tool_call`
   的第 2 跳能真正执行到 tool（不是只有命令表声明）。

判据有效性：本文件的用例在 #202 代码合入前**跑不起来**（`mcp_call` 不存在），所以
「变绿」只能由这次改动造成；`test_call_reaches_local_http_target` 与
`test_console_second_hop_executes_mcp_call` 是对着真实 HTTP 响应断言的，不是对着
mock 的返回值。

第 2b 组（`test_concurrent_calls_do_not_cross_talk` / `test_oversized_result_is_really_truncated`
/ `test_non_json_response_is_distinguishable` / `test_build_argv_rejects_unsupported_and_unknown_extra`）
是验收打回项 B1/B3/B5(+B4) 的判据，**每一条都在改前代码上实跑过并红**，读数写在各
用例 docstring 里。
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from exception import ForwardToolCall
from service.agentService import toolRegistry
from service.funcToolService import core as func_tool_core
from service.funcToolService import mcpTool
from service.funcToolService.consoleTool import console
from service.funcToolService.tools import mcp_call
from util import llmApiUtil


# ---------------------------------------------------------------------------
# 夹具：本地 spec + 本地静态 HTTP 服务（真发请求，但不出网）
# ---------------------------------------------------------------------------

_SPEC_TEMPLATE = {
    "openapi": "3.0.0",
    "info": {"title": "Local Demo API", "version": "1.0.0"},
    "paths": {
        "/thing": {
            "get": {
                "operationId": "getThing",
                "summary": "Get a demo thing",
                "responses": {"200": {"description": "ok"}},
            }
        },
        # 带路径参数的一条：`item_id`（下划线）→ mcp2cli 的 `--item-id`（kebab），
        # 用来把「参数名归一」钉成行为判据。
        "/items/{item_id}": {
            "get": {
                "operationId": "getItemById",
                "summary": "Get an item by id",
                "parameters": [
                    {"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


@pytest.fixture
def served_dir(tmp_path: Path):
    """在本地起一个静态服务，返回 (目录, base_url)。目录里的文件就是 HTTP 响应体。"""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield tmp_path, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def dynamic_server():
    """按路径返回定制响应，用于截断 / 非 JSON / 可区分性 / 飞行期类判据。

    `routes[path]` 可以是 `(body, content_type)` 或 `(body, content_type, delay_seconds)`。
    带 delay 是为了「飞行期」判据真的成立：**没有真实延迟，被测调用会在噪声发生前就
    结束，判据会变成恒绿空真**（实测踩过这个坑 —— 摘掉修复后判据照样绿）。
    """

    routes: dict[str, tuple] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            key = self.path.split("?")[0].strip("/")
            entry = routes.get(key, (b"{}", "application/json"))
            body, ctype = entry[0], entry[1]
            delay = entry[2] if len(entry) > 2 else 0.0
            if delay:
                time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 别把访问日志写到测试输出里
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield routes, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_spec(directory: Path, base_url: str) -> str:
    spec = json.loads(json.dumps(_SPEC_TEMPLATE))
    spec["servers"] = [{"url": base_url}]
    path = directory / "openapi.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def _write_spec_with_paths(directory: Path, base_url: str, name: str, paths: list[str]) -> str:
    """额外 spec：每条 path 生成一个 `get-<slug>` 工具（同 static 服务的文件名对应）。"""
    spec = json.loads(json.dumps(_SPEC_TEMPLATE))
    spec["servers"] = [{"url": base_url}]
    for raw in paths:
        slug = raw.strip("/")
        spec["paths"][raw] = {
            "get": {
                "operationId": "get" + slug.replace("-", " ").title().replace(" ", ""),
                "summary": slug,
                "responses": {"200": {"description": "ok"}},
            }
        }
    path = directory / name
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 1. 纯函数：argv 形态
# ---------------------------------------------------------------------------

def test_build_argv_list_uses_source_flag_json_and_list():
    argv = mcpTool.build_argv(
        kind="openapi", target="./openapi.json", action="list", tool=None, args=None, extra=None
    )
    assert argv == ["--spec", "./openapi.json", "--json", "--list"]


def test_build_argv_call_maps_args_to_kebab_flags_and_booleans():
    """`args` 的键按 mcp2cli 的口径 kebab-case；bool 走 `--flag` / `--no-flag`。

    同时钉住**段序**：`extra` 是全局开关，必须落在工具名之前（上游
    `_split_at_subcommand` 在工具名处切段，工具段里的全局开关会让子 parser `exit(2)`）。
    """
    argv = mcpTool.build_argv(
        kind="mcp",
        target="http://127.0.0.1:9/mcp",
        action="call",
        tool="read-file",
        args={"file_path": "/tmp/a.txt", "verbose": True, "dry_run": False, "n": 3},
        extra={"head": 5, "transport": "sse"},
    )
    assert argv[:7] == [
        "--mcp",
        "http://127.0.0.1:9/mcp",
        "--json",
        "--head",
        "5",
        "--transport",
        "sse",
    ], argv
    assert argv[7:9] == ["read-file", "--file-path"], argv
    assert "/tmp/a.txt" in argv
    assert "--verbose" in argv and "--no-dry-run" in argv
    assert "--n" in argv and "3" in argv


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        (dict(kind="nope", target="x"), "mcp_invalid_kind"),
        (dict(kind="openapi", target="   "), "mcp_invalid_target"),
        (dict(kind="openapi", target="x", action="call"), "mcp_missing_tool"),
        (dict(kind="openapi", target="x", action="explode"), "mcp_invalid_action"),
    ],
)
def test_build_argv_rejects_bad_input_with_structured_codes(kwargs, expected_code):
    full = dict(kind="openapi", target="x", action="list", tool=None, args=None, extra=None)
    full.update(kwargs)
    with pytest.raises(mcpTool.McpCallError) as excinfo:
        mcpTool.build_argv(**full)
    assert excinfo.value.error_code == expected_code


# ---------------------------------------------------------------------------
# 2. 实跑：本地 spec 的 list / call，以及「坏 spec 不许带走进程」
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_local_spec_returns_commands(tmp_path: Path):
    spec_path = _write_spec(tmp_path, "http://127.0.0.1:9")
    result = await mcp_call(target=spec_path, action="list")

    assert result["success"] is True, result
    names = [cmd["name"] for cmd in result["result"]]
    assert names == ["get-thing", "get-item-by-id"], f"list 没解析出命令：{names}"


@pytest.mark.asyncio
async def test_call_reaches_local_http_target(served_dir):
    """`action=call` 必须**真发一次 HTTP**并把响应体解析回 `result`。"""
    directory, base_url = served_dir
    (directory / "thing").write_text(json.dumps({"id": "thing-1", "name": "demo"}), encoding="utf-8")
    spec_path = _write_spec(directory, base_url)

    result = await mcp_call(target=spec_path, action="call", tool="get-thing")

    assert result["success"] is True, result
    assert result["result"] == {"id": "thing-1", "name": "demo"}


@pytest.mark.asyncio
async def test_missing_spec_fails_structured_without_killing_process(tmp_path: Path):
    """mcp2cli 的错误路径会 `sys.exit(1)`：必须被兜成结构化失败。

    判据是「函数正常返回 dict」——如果 `SystemExit` 漏上去，pytest 这条会直接
    被中断（进程/用例级异常），而不是给出断言失败。
    """
    missing = str(tmp_path / "nope.json")
    result = await mcp_call(target=missing, action="list")

    assert result["success"] is False
    assert result["error_code"] in {"mcp2cli_exit", "mcp2cli_error"}, result
    assert result.get("message")


@pytest.mark.asyncio
async def test_call_to_unknown_tool_fails_structured(served_dir):
    directory, base_url = served_dir
    spec_path = _write_spec(directory, base_url)

    result = await mcp_call(target=spec_path, action="call", tool="no-such-tool")

    assert result["success"] is False
    assert result["error_code"] == "mcp2cli_exit"
    assert result["exit_code"] != 0


# ---------------------------------------------------------------------------
# 2b. 复验打回项：并发串味 / 假截断 / 非 JSON 不可区分 / 静默失效的 extra
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_calls_do_not_cross_talk(tmp_path: Path, dynamic_server):
    """B1：两路并发调**不同** target，各自必须拿到自己的结果。

    改前实测（2 路 ×40 批）：`mcp2cli_empty_output` 36 次、`mcp2cli_bad_json` 17 次、
    **19 次拿到另一个 target 的结果**、只有 8 次对。根因是 `redirect_stdout` 换的是
    进程级 `sys.stdout`，而 `to_thread` 让两个调用真正并发。

    判据为什么能咬住：check 的是「B 的结果里不许出现 A 的标记」，串味时会以
    `success=True` + 错内容的形式返回 —— 只断言 `success` 是抓不到的。
    """
    routes, base_url = dynamic_server
    routes["a"] = (json.dumps({"marker": "AAA"}).encode(), "application/json")
    routes["b"] = (json.dumps({"marker": "BBB"}).encode(), "application/json")
    spec_a = _write_spec_with_paths(tmp_path, base_url, "spec_a.json", ["/a"])
    spec_b = _write_spec_with_paths(tmp_path, base_url, "spec_b.json", ["/b"])

    for _ in range(20):
        got_a, got_b = await asyncio.gather(
            mcp_call(target=spec_a, action="call", tool="get-a"),
            mcp_call(target=spec_b, action="call", tool="get-b"),
        )
        assert got_a.get("success") is True, got_a
        assert got_b.get("success") is True, got_b
        assert got_a["result"] == {"marker": "AAA"}, got_a
        assert got_b["result"] == {"marker": "BBB"}, got_b


@pytest.mark.asyncio
async def test_oversized_result_is_really_truncated(tmp_path: Path, dynamic_server):
    """B3：超上限时 `result` **不下发**，只给 `raw_excerpt` + `original_chars`。

    改前：`truncated=True` 但 `result` 里仍是完整 payload（实测 60029 字符整份进上下文）
    —— 保护上下文只写在注释里，「已截断」的旗号反而误导成安全。
    """
    routes, base_url = dynamic_server
    blob = "x" * 60000
    routes["big"] = (json.dumps({"blob": blob}).encode(), "application/json")
    spec = _write_spec_with_paths(tmp_path, base_url, "spec_big.json", ["/big"])

    result = await mcp_call(target=spec, action="call", tool="get-big")

    assert result["success"] is True, result
    assert result["truncated"] is True, result
    assert "result" not in result, "超限时不应下发 result（否则截断没意义）"
    assert result["original_chars"] > mcpTool.MAX_OUTPUT_CHARS, result
    assert result["raw_excerpt"] is not None
    assert len(result["raw_excerpt"]) <= mcpTool.MAX_OUTPUT_CHARS + 100, len(result["raw_excerpt"])


@pytest.mark.asyncio
async def test_non_json_response_is_distinguishable(tmp_path: Path, dynamic_server):
    """B5：远端回纯文本时必须能从返回体看出「这不是 JSON」，而不是静默当成功字符串。

    改前：`success=True` + `result="this is NOT json <<<"`，与「远端确实返回了一个 JSON
    字符串」无法区分；原计划用 `extra={"raw": True}` 走 `sys.stdout.buffer` 分流，但
    上游 `output_result` 里 `--json` 分支在 `raw` 之前 return ⇒ 那条路径是死代码。
    现方案：`result_type` 标类型 + `raw` / `toon` 直接拒（`mcp_unsupported_extra`）。
    """
    routes, base_url = dynamic_server
    routes["text"] = (b"this is NOT json <<<", "text/plain")
    spec = _write_spec_with_paths(tmp_path, base_url, "spec_text.json", ["/text"])

    result = await mcp_call(target=spec, action="call", tool="get-text")
    assert result["success"] is True, result
    assert result["result"] == "this is NOT json <<<"
    assert result["result_type"] == "string", result

    # 对照：同一套判据下 JSON 对象必须是 "object"（防止把 result_type 写成常量）。
    routes["obj"] = (json.dumps({"marker": "OBJ"}).encode(), "application/json")
    spec_obj = _write_spec_with_paths(tmp_path, base_url, "spec_obj.json", ["/obj"])
    ok = await mcp_call(target=spec_obj, action="call", tool="get-obj")
    assert ok["result_type"] == "object", ok

    # `raw` 走不通（上游 --json 优先），必须是**显式拒绝**而不是静默忽略后返回全量。
    rejected = await mcp_call(
        target=spec, action="call", tool="get-text", extra={"raw": True}
    )
    assert rejected["success"] is False
    assert rejected["error_code"] == "mcp_unsupported_extra", rejected


def test_build_argv_rejects_unsupported_and_unknown_extra():
    """B4：静默失效的 extra 必须报错，而不是收下、忽略、回全量。

    `head` 在 `kind=openapi` 下上游根本没传给 `execute_openapi`（实测数组响应
    `--head 1` 仍返回 5 条）；`raw` / `toon` 被固定 `--json` 挡住。这三类都要拒。
    """
    base = dict(
        kind="openapi", target="/tmp/x.json", action="call", tool="get-x", args=None
    )
    for extra, code in [
        ({"head": 1}, "mcp_unsupported_extra"),
        ({"raw": True}, "mcp_unsupported_extra"),
        ({"toon": True}, "mcp_unsupported_extra"),
        ({"no-such-flag": True}, "mcp_invalid_extra"),
    ]:
        with pytest.raises(mcpTool.McpCallError) as excinfo:
            mcpTool.build_argv(extra=extra, **base)
        assert excinfo.value.error_code == code, (extra, excinfo.value.error_code)


@pytest.mark.asyncio
async def test_noise_from_other_coroutine_does_not_break_the_call(tmp_path: Path, dynamic_server):
    """B2：飞行期**别的协程** print 不许污染本次调用，也不许被吞掉。

    改前实测（`fdf70c29` 版本同样成立，属既有缺陷、与 B1 同源）：慢调用在飞时另一协程
    `print` 一次 ⇒ `out` 变成 `噪音 + JSON`，`json.loads` 失败 ⇒ 返回值是
    `{'success': False, 'error_code': 'mcp2cli_bad_json', 'raw_excerpt': 'POLLUTION_MARKER…'}`。
    也就是说**后端任何一条日志都能让 mcp_call 失败**，这条比「日志被吞」严重得多。

    三条对照一起断言，防「把成功写成常量」：
      无噪音 ⇒ 成功；噪音在调用前 ⇒ 成功；噪音在飞行期 ⇒ 仍然成功且 marker 出现在真实流。
    """
    routes, base_url = dynamic_server
    # 1 秒延迟 + 噪声在 0.3s：确保噪声**确实落在飞行期内**。没有真实延迟时调用会在
    # 噪声发生前就结束，判据恒绿 —— 实测踩过：摘掉修复后判据照样通过。
    routes["slow"] = (json.dumps({"marker": "SLOW"}).encode(), "application/json", 1.0)
    spec = _write_spec_with_paths(tmp_path, base_url, "spec_slow.json", ["/slow"])
    marker = "NOISE_MARKER_4b8e"

    async def call():
        return await mcp_call(target=spec, action="call", tool="get-slow")

    # 对照 1：无噪音
    assert (await call()).get("success") is True

    # 对照 2：噪音在调用之前
    print(marker + "_BEFORE")
    assert (await call()).get("success") is True

    # 实验：噪音在飞行期（另一协程）
    async def noise_during_flight():
        await asyncio.sleep(0.3)
        print(marker + "_DURING")

    inflight, _ = await asyncio.gather(call(), noise_during_flight())

    assert inflight.get("success") is True, inflight
    assert inflight["result"] == {"marker": "SLOW"}, inflight
    assert inflight.get("error_code") is None, inflight


# ---------------------------------------------------------------------------
# 3. 接入面：console 转发 + 第 2 跳真执行
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_console_mcp_ls_forwards_to_mcp_call(tmp_path: Path):
    spec_path = _write_spec(tmp_path, "http://127.0.0.1:9")

    with pytest.raises(ForwardToolCall) as excinfo:
        await console(f"mcp ls {spec_path}")

    forward = excinfo.value
    assert forward.tool_name == "mcp_call"
    assert forward.tool_args == {"target": spec_path}


@pytest.mark.asyncio
async def test_console_mcp_call_forwards_action_and_options(tmp_path: Path):
    spec_path = _write_spec(tmp_path, "http://127.0.0.1:9")

    with pytest.raises(ForwardToolCall) as excinfo:
        await console(f"mcp call {spec_path} --tool get-thing")

    forward = excinfo.value
    assert forward.tool_name == "mcp_call"
    # `action` 由命令表的 `input_defaults` 补上：命令语义就是 call，调用方不必再写。
    assert forward.tool_args == {"action": "call", "tool": "get-thing", "target": spec_path}


@pytest.mark.asyncio
async def test_console_second_hop_executes_mcp_call(served_dir):
    """`console mcp ls ...` 经 `execute_tool_call` 第 2 跳真的执行到 `mcp_call`。

    只测「命令表里声明了 legacy_tool」不够——注册面漏了工具（#198 那个坑）时命令表
    照样对，第 2 跳却会返回「未知工具」。这里对着真实解析结果断言。
    """
    directory, base_url = served_dir
    (directory / "thing").write_text(json.dumps({"id": "thing-1"}), encoding="utf-8")
    spec_path = _write_spec(directory, base_url)

    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools():
        registry.register(tool, func_tool_core.run_tool_call)

    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc_mcp_hop",
        function={"name": "console", "arguments": json.dumps({"command": f"mcp ls {spec_path}"})},
    )

    result = await registry.execute_tool_call(tool_call, context=ctx)
    payload = result.result if hasattr(result, "result") else result

    assert payload.get("success") is True, payload
    assert [cmd["name"] for cmd in payload["result"]] == ["get-thing", "get-item-by-id"]

    # 带参数的那条也要走一遍：`args` 的键是工具签名里的 `item_id`，必须被归一成
    # mcp2cli 认的 `--item-id` —— 这条把「kebab 归一」从纯函数判据升级为行为判据
    # （改回去就会在 argparse 处报 required 缺失，而不是只在单测里比对字符串）。
    (directory / "items" / "7").parent.mkdir(exist_ok=True)
    (directory / "items" / "7").write_text(json.dumps({"id": "item-7"}), encoding="utf-8")
    call = llmApiUtil.OpenAIToolCall(
        id="tc_mcp_hop_call",
        function={
            "name": "console",
            "arguments": json.dumps({
                "command": f"mcp call {spec_path} --tool get-item-by-id --args '{{\"item_id\": \"7\"}}'"
            }),
        },
    )
    payload = await registry.execute_tool_call(call, context=ctx)
    payload = payload.result if hasattr(payload, "result") else payload

    assert payload.get("success") is True, payload
    assert payload["result"] == {"id": "item-7"}
