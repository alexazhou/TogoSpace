"""mcp2cli 接入（#202）：把任意 MCP / OpenAPI / GraphQL 服务压成**一条**工具入口。

## 为什么要这一层

平台原来的外呼形态是「每接一个外部 API 就把整套 tool schema 喂给模型」。mcp2cli
在运行时把任意 MCP server / OpenAPI spec / GraphQL endpoint 转成 CLI，模型只需要记住
`mcp_call` 一个工具 + `--list` 一次拿命令清单，schema 开销从「N 套」降到「1 套」。

## 执行方式：库调用；openapi / mcp / graphql 三态零子进程，mcp_stdio 起子进程

`mcp2cli` 是 CLI 优先的项目，但它把分派逻辑抽成了 `_main_impl(argv)`——**读的是入参
而不是 `sys.argv`**，因此可以在进程内直接调，不必 `Popen(sys.executable, ...)`。
这躲开了 PyInstaller onedir 下「打包后的可执行文件当 python 解释器」的坑（方案 §4：
只有 `--session-start` daemon 会用 `sys.executable -c`，本单不用 session 模式）。

**「零子进程」只对 openapi / mcp / graphql 三态成立**：它们走 HTTP，进程内完成。
`mcp_stdio` 例外——它按 Operator 要求保留，上游 `mcp.client.stdio.stdio_client` 会
真的拉起本地子进程（这是该 kind 的定义，不是本层引入的开销）。

调 `_main_impl` 而不是逐个拼公开函数（`load_openapi_spec` / `handle_mcp` /
`execute_openapi` …）的原因：OpenAPI 的 `call` 分支要先用 `_build_main_parser()`
建全局 parser、再 `build_argparse()` 出子命令 parser、再 `execute_openapi(args, …)`，
自己拼等于把 mcp2cli 的分派逻辑抄一遍，抄完就与上游版本漂移。代价是**依赖了一个
下划线开头的函数**：升级 mcp2cli 时必须复跑 `tests/unit/service/funcToolService/
test_mcp_call.py` 的实跑用例。公开函数链（`load_openapi_spec → extract_openapi_commands
→ list_openapi_commands(json_output=True)`）已单独复验可用，作为届时改写的退路。

## 三个必须处理的点（都是「不跑崩」的技术必需，不是安全加固）

1. mcp2cli 靠 `print` 出结果、**不 return** ⇒ 捕获 stdout/stderr；
2. 错误路径会 `sys.exit(1)` ⇒ `try/except SystemExit` 兜住，**绝不让它杀掉后端进程**；
3. 函数是**同步阻塞**的（起 HTTP 请求 / 本地 stdio 子进程）⇒ `asyncio.to_thread`
   丢线程池，别卡事件循环。

## 并发约束与**可用性代价**（#202 复验 B1）

第 1 点的捕获手段是替换**进程级** `sys.stdout`，而 `to_thread` 让多个调用真并发 ⇒
两个调用会互相截获对方的输出（实测 2 路并发 40 批：19 次拿到**另一个 target** 的
结果）。所以模块级加一把 `threading.Lock`，把「替换 stdout → 跑 → 取回」整段串行化：
同时只有一次 mcp2cli 在写那个全局流。

**代价（必须知情，不许当白送）**：`DEFAULT_TIMEOUT_SECONDS = 120`，因此**一条慢调用
最多能把进程内所有 `mcp_call` 堵 120 秒**。实测读数见实现报告 §5.3（1 个慢 target
在飞时另一快 target 的耗时）。收窄这个代价的两条路各自的问题：

- 「每次调用独立捕获」做不到：`print` 写的是 `sys.stdout` 这个**全局名字**，不是
  可注入的句柄，除非改上游或每调用开子进程；
- 「走上游公开函数链」代价更大：OpenAPI 的 call 分支要先 `_build_main_parser()` 再
  `build_argparse()` 再 `execute_openapi()`，自己拼 = 抄一遍上游分派逻辑（见上节），
  且公开函数链同样 `print` 到 `sys.stdout`，**串味问题一模一样**。

所以串行化是当前形态下「不串味」的最小代价方案，**不是因为它没有代价**。

残余风险（已处理一部分）：持锁期间 `sys.stdout` / `sys.stderr` 仍是我们的捕获流，
但捕获流按**线程**过滤（`_ThreadIsolatedStream`）——只有跑 mcp2cli 的那个 worker
线程的写入进结果，其它线程的 `print` / 日志原样转发到真实流。因此「别的协程日志被吞」
与「日志污染 JSON 输出」两个问题都已消除（实测读数见实现报告 §5.4）。
仍存在的残余：`redirect_stdout` 期间 `sys.stdout` 这个**对象**确实被换掉了（若别处
缓存了 `sys.stdout` 的旧引用、或依赖 `isinstance(sys.stdout, TextIOWrapper)` 判断，
会看到替身）。要彻底消除得改上游或每调用开子进程，超出本单范围。
回调给模型的 `truncated` / `result_type` 都按真实行为给，不按注释里的意图给。
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import sys
import threading
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from typing import TextIO

logger = logging.getLogger(__name__)

#: 串行化「替换进程级 sys.stdout/stderr」的那一段（见模块 docstring「并发约束」）。
_STDOUT_LOCK = threading.Lock()

#: 允许的来源类型 → mcp2cli 的全局开关。
#: `mcp_stdio` 对应 `--mcp-stdio`：Operator 2026-11-13 02:11 明确「不要禁用」，
#: 所以这里和其它来源一样放行（本单不做任何白名单/禁用逻辑）。
_SOURCE_FLAGS: dict[str, str] = {
    "openapi": "--spec",
    "mcp": "--mcp",
    "mcp_stdio": "--mcp-stdio",
    "graphql": "--graphql",
}

#: 回给模型的输出上限（字符）。超了截断并置 `truncated=True`。
#: 远端 API 返回原文进上下文，不截断一次大响应就能把上下文打满。
MAX_OUTPUT_CHARS = 20000

#: 单次调用的默认超时（秒）。库调用阻塞在线程里，超时只能「不再等」，
#: 线程本身会随请求结束自行退出。
DEFAULT_TIMEOUT_SECONDS = 120


class McpCallError(Exception):
    """mcp2cli 调用失败（带平台侧 error_code，便于模型按码自纠）。"""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _global_option_names() -> set[str] | None:
    """mcp2cli 全局选项名（含短选项）；取不到返回 None（表示「不校验」）。

    `extra` 只能放**全局**选项：上游 `_split_at_subcommand` 在遇到第一个位置参数
    （工具名）时就把 argv 切成「全局段 / 工具段」，工具段里的选项只认该工具自己的
    参数。放错位置的全局选项不会报「未知开关」，而是让工具子 parser 直接 `exit(2)`
    并喷一整页 usage —— 对模型来说既看不出原因又烧 token。所以这里提前把名字校验
    掉，错就给 `mcp_invalid_extra`。
    """
    try:
        import mcp2cli

        parser = mcp2cli._build_main_parser()  # noqa: SLF001 —— 与 `_main_impl` 同源
    except Exception:  # noqa: BLE001 —— 拿不到就当没有校验能力，不阻断调用
        return None
    names: set[str] = set()
    for action in parser._actions:  # noqa: SLF001
        names.update(action.option_strings)
    return names

def _cli_name(key: str) -> str:
    """工具参数名 → mcp2cli 的命令行参数名。

    上游把参数名统一 kebab-case（`thing_id` → `--thing-id`，见 `--list --json` 里
    每参数的 `name`）。这里用上游自己的 `to_kebab` 保持同一口径；取不到时退回
    下划线替换，保证「模型按 list 出来的名字写」和「按工具签名写」两种都能通。
    """
    try:
        import mcp2cli

        return mcp2cli.to_kebab(key)
    except Exception:  # noqa: BLE001 —— 兜底不能让拼参数这一步崩掉
        return str(key).replace("_", "-")


def _flatten_args(args: dict[str, Any]) -> list[str]:
    """把 `args` dict 摊成 mcp2cli 的 `--参数 值` 形态。

    布尔按 mcp2cli 的口径走开关：`True` → `--flag`、`False` → `--no-flag`
    （见上游 `build_argparse`：`--<name>` 发 true、`--no-<name>` 发 false）。
    其余非字符串值一律 JSON 编码 —— 对象/数组参数在上游就是按 JSON 解析的。
    """
    argv: list[str] = []
    for key, value in (args or {}).items():
        flag = f"--{_cli_name(key)}"
        if isinstance(value, bool):
            argv.append(flag if value else f"--no-{_cli_name(key)}")
        elif isinstance(value, str):
            argv.extend([flag, value])
        else:
            argv.extend([flag, json.dumps(value, ensure_ascii=False)])
    return argv


def build_argv(
    *,
    kind: str,
    target: str,
    action: str,
    tool: str | None,
    args: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> list[str]:
    """按 mcp2cli 的 CLI 语法拼出 argv（纯函数，便于单测直接断言）。

    **选项顺序是有语义的**：上游 `_split_at_subcommand` 在遇到第一个位置参数（工具名）
    处把 argv 切成「全局段 / 工具段」，工具段里的选项只认该工具自己的参数。所以
    `extra` 里的全局开关必须排在工具名**之前** —— 排在后面不会报「未知开关」，而是让
    工具子 parser `exit(2)` 并喷一整页 usage（实测：`get-a --head 1` → 退出码 2）。
    """
    flag = _SOURCE_FLAGS.get(kind)
    if flag is None:
        raise McpCallError(
            "mcp_invalid_kind",
            f"kind 只支持 {' / '.join(_SOURCE_FLAGS)}，收到 {kind!r}。",
        )
    if not target or not str(target).strip():
        raise McpCallError("mcp_invalid_target", "target 不能为空（填 spec 路径 / MCP URL / GraphQL URL）。")

    normalized = (action or "list").strip().lower()
    # `--json` 属于全局段，必须排在工具名之前（理由同 extra）。固定带上它是因为
    # 只有 JSON 才能被本层解析成 `result`；它的代价见 `_SHADOWED_BY_JSON`。
    argv: list[str] = [flag, str(target), "--json"]

    # extra：全局开关，必须落在工具名之前（见 docstring）。
    known = _global_option_names()
    for key, value in (extra or {}).items():
        if value is None or value is False:
            continue
        reason = _extra_unsupported_reason(key, kind)
        if reason is not None:
            raise McpCallError("mcp_unsupported_extra", f"extra[{key!r}] 不生效：{reason}")
        name = f"--{key}"
        if known is not None and name not in known:
            raise McpCallError(
                "mcp_invalid_extra",
                f"extra 里有 mcp2cli 不认识的全局开关 {name!r}（可用：{sorted(known)[:8]} …）。",
            )
        if value is True:
            argv.append(name)
        elif isinstance(value, str):
            argv.extend([name, value])
        else:
            argv.extend([name, json.dumps(value, ensure_ascii=False)])

    if normalized == "list":
        argv.append("--list")
    elif normalized == "call":
        if not tool or not str(tool).strip():
            raise McpCallError("mcp_missing_tool", "action=call 时必须给 tool（工具名）。")
        argv.append(str(tool).strip())
        argv.extend(_flatten_args(args or {}))
    else:
        raise McpCallError("mcp_invalid_action", f"action 只支持 list / call，收到 {action!r}。")
    return argv


#: 与固定 `--json` 冲突、传了也不生效的全局开关（上游 `output_result` 里
#: `if json_output:` 分支在 `if raw:` / `toon` 之前 return）。
_SHADOWED_BY_JSON = ("raw", "toon")

#: `kind` → 该来源下「上游收下了但根本没用」的开关。
#: 只收录**实测/读码确认**的项，不猜。
_IGNORED_EXTRA: dict[str, dict[str, str]] = {
    # `_handle_openapi_mode` 调 `execute_openapi(...)` 时没有传 `head`，
    # 而 `execute_openapi` 的签名里那个 `head` 只用于 `output_result`。
    "openapi": {
        "head": "openapi（HTTP）返回的是单个对象，上游 `_handle_openapi_mode` 不把 --head 传给 execute_openapi。",
    },
}


def _extra_unsupported_reason(key: str, kind: str) -> str | None:
    """`extra` 里这个开关是否**确定不生效**；是则返回给模型看的原因。

    为什么要主动拒：模型照着工具说明写了 `extra={"head": 1}`，mcp2cli 会收下它、
    悄悄忽略，然后回一份**完整**响应 —— 模型以为限了量、实际没限，最后撞上截断。
    静默无效比报错更贵，所以这类开关直接给 `mcp_unsupported_extra`。
    """
    if key in _SHADOWED_BY_JSON:
        return f"本工具固定带 --json，上游 output_result 会先走 --json 分支，--{key} 永不生效。"
    return _IGNORED_EXTRA.get(kind, {}).get(key)


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + f"\n…（已截断，原文 {len(text)} 字符）", True


def _result_type(payload: Any) -> str:
    """`result` 的 JSON 类型标签。

    mcp2cli 在 `--json` 下把「响应体不是 JSON」也包成合法 JSON——`output_result` 里
    `json.loads` 失败就 `_emit_json` 原样输出，于是 `this is NOT json` 变成字符串
    `"this is NOT json"`。所以 `result` 是 `str` **不代表失败**，只代表远端回的是
    纯文本；标出类型，模型才能区分「拿到一段文本」和「拿到一个 JSON 对象」。
    """
    if isinstance(payload, dict):
        return "object"
    if isinstance(payload, list):
        return "array"
    if isinstance(payload, str):
        return "string"
    if isinstance(payload, bool):
        return "boolean"
    if payload is None:
        return "null"
    return "number"


class _ThreadIsolatedStream(io.TextIOBase):
    """按线程隔离的替身流：只有 **owner 线程**（跑 mcp2cli 的那个）的写入被捕获，
    其它线程的写入**原样转发**给真实流。

    为什么必须隔离（#202 复验 B2，实测）：`redirect_stdout` 换的是进程级
    `sys.stdout`，所以飞行期里**任何**协程的 `print` / 日志都会落进捕获流。后果有两个，
    第二个是致命的：

    1. 那些日志从真实 stdout 消失（「被吞」）；
    2. 如果它落在 mcp2cli 输出**之前**，`out` 就变成 `噪音 + JSON`，`json.loads` 失败
       ⇒ 一次本来正常的调用被报成 `mcp2cli_bad_json`。实测复现：慢调用在飞时另一协程
       `print` 一次，返回值就是 `{'success': False, 'error_code': 'mcp2cli_bad_json',
       'raw_excerpt': 'POLLUTION_MARKER_...\\n{"marker": "SLOW"}'}`。
       对照三条：无噪音 ⇒ 成功；噪音发生在取锁前 ⇒ 成功；噪音发生在飞行期 ⇒ 失败。

    mcp2cli 跑在 `asyncio.to_thread` 的 worker 线程里，别的协程跑在事件循环线程里，
    所以 `threading.get_ident()` 足以区分两者。噪音转发回真实流 = 顺带把「被吞」也修掉。
    """

    #: 声明为 `TextIO` —— `contextlib.redirect_stdout` 的签名要求真实文本流类型，
    #: 用 `TextIOBase` 过不了 mypy 的类型变量检查。
    encoding: str = "utf-8"
    errors: str = "replace"
    newlines: None = None
    mode: str = "w"
    name: str = "<mcpTool capture>"

    def __init__(self, passthrough: Any) -> None:
        super().__init__()
        self._owner = threading.get_ident()
        self._buf = io.StringIO()
        self._passthrough = passthrough

    def write(self, text: str) -> int:  # type: ignore[override]
        if threading.get_ident() == self._owner:
            return self._buf.write(text)
        # 别人的输出照常可见（不让它消失，也不让它进结果）
        self._passthrough.write(text)
        return len(text)

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def flush(self) -> None:
        self._passthrough.flush()

    def isatty(self) -> bool:  # 上游 _emit_json 据此决定是否缩进
        return False

    def getvalue(self) -> str:
        return self._buf.getvalue()


def _run_argv_sync(argv: list[str]) -> dict[str, Any]:
    """同步跑一次 mcp2cli 分派（在线程里执行）。返回结构化结果，不抛异常。

    **整段持 `_STDOUT_LOCK`**：`redirect_stdout` 换的是进程级 `sys.stdout`，两个线程
    同时进来就会互相截获输出。锁加在这里（而不是 `call_mcp` 里 `async with`）是因为
    锁的粒度必须覆盖「替换 → 跑 → 取回」三步。

    捕获流用 `_ThreadIsolatedStream`：持锁只挡得住**另一个 mcp_call**，挡不住同进程里
    别的协程在飞行期 `print`（那会把 JSON 输出污染成非法 JSON）。按线程过滤才是对症的。
    """
    stdout = _ThreadIsolatedStream(sys.__stdout__)
    stderr = _ThreadIsolatedStream(sys.__stderr__)
    # `redirect_stdout` 的类型签名要求 `TextIO`，而本类是按需实现的那一小部分协议
    # （write / flush / isatty / writable）；运行时行为由本文件的用例覆盖，这里只让
    # 类型检查器知情。用 cast 而不是伪造几十个 TextIO 属性 —— 假的实现更难维护。
    stdout_io = cast("TextIO", stdout)
    stderr_io = cast("TextIO", stderr)

    try:
        import mcp2cli

        dispatcher = getattr(mcp2cli, "_main_impl", None)
        if dispatcher is None:  # 上游改名 ⇒ 报错要能指认，而不是 AttributeError
            return {
                "success": False,
                "error_code": "mcp2cli_dispatcher_missing",
                "message": "mcp2cli 未提供 _main_impl，库调用路径失效（版本变了？）",
            }
        with _STDOUT_LOCK, contextlib.redirect_stdout(stdout_io), contextlib.redirect_stderr(stderr_io):
            dispatcher(list(argv))
    except SystemExit as exc:  # mcp2cli 的错误路径：sys.exit(1) 绝不能杀后端进程
        code = exc.code if isinstance(exc.code, int) else 1
        return {
            "success": False,
            "error_code": "mcp2cli_exit",
            "message": f"mcp2cli 以退出码 {code} 结束（参数或目标有误）。",
            "exit_code": code,
            "stderr": _truncate(stderr.getvalue())[0],
        }
    except Exception as exc:  # noqa: BLE001 —— 任何异常都转结构化失败，不往上冒
        logger.warning("mcp2cli 调用异常: %r", exc)
        return {
            "success": False,
            "error_code": "mcp2cli_error",
            "message": f"{type(exc).__name__}: {exc}",
            "stderr": _truncate(stderr.getvalue())[0],
        }

    out = stdout.getvalue()
    err = stderr.getvalue()
    if not out.strip():
        return {
            "success": False,
            "error_code": "mcp2cli_empty_output",
            "message": "mcp2cli 没有输出任何内容。",
            "stderr": _truncate(err)[0],
        }
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        # **防御性分支，当前不可达**（#202 复验 B5）：本工具固定带 `--json`，而上游
        # `output_result` 在 `json_output=True` 时会把任何非 JSON 响应体包成合法 JSON
        # 字符串再输出（`json.loads` 失败 → `_emit_json` 原样输出）。所以 `out` 走到
        # 这里只能说明「上游改了这层保证」。保留它是因为届时若没有这个兜底，模型会收到
        # 一个无法解释的空返回；但**不要当能力宣称**：非 JSON 响应请按返回体的
        # `result_type == "string"` 判断。
        body, cut = _truncate(out)
        return {
            "success": False,
            "error_code": "mcp2cli_bad_json",
            "message": f"输出不是合法 JSON：{exc.msg}（已固定 --json，此分支应为防御性）",
            "raw_excerpt": body,
            "truncated": cut,
            "stderr": _truncate(err)[0],
        }

    # 真截断：超限时**不下发 result**。只截 `raw_excerpt` 而照样把完整 payload 塞进
    # `result`，等于把「保护上下文」写成了一句注释 —— 实测 60029 字符的 `result` 就是
    # 这样整份进了模型上下文，`truncated=True` 反而误导成「已经安全」。
    full = json.dumps(payload, ensure_ascii=False)
    result_type = _result_type(payload)
    if len(full) > MAX_OUTPUT_CHARS:
        body, _ = _truncate(full)
        return {
            "success": True,
            "truncated": True,
            "original_chars": len(full),
            "result_type": result_type,
            "raw_excerpt": body,
            "message": (
                f"结果 {len(full)} 字符超过上限 {MAX_OUTPUT_CHARS}，已截断为 raw_excerpt"
                "（result 不下发）。可缩小查询范围或用 extra 里的 --head / --fields 限量。"
            ),
            "stderr": _truncate(err)[0] or None,
        }
    return {
        "success": True,
        "result": payload,
        "result_type": result_type,
        "truncated": False,
        "raw_excerpt": None,
        "stderr": _truncate(err)[0] or None,
    }


async def call_mcp(
    *,
    kind: str,
    target: str,
    action: str = "list",
    tool: str | None = None,
    args: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """跑一次 mcp2cli 调用并把结果收成工具返回体（不抛异常）。"""
    try:
        argv = build_argv(kind=kind, target=target, action=action, tool=tool, args=args, extra=extra)
    except McpCallError as exc:
        return {"success": False, "error_code": exc.error_code, "message": exc.message}

    logger.info("mcp_call: argv=%r", argv)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_argv_sync, argv), timeout=timeout
        )
    except asyncio.TimeoutError:
        return {
            "success": False,
            "error_code": "mcp_call_timeout",
            "message": f"mcp2cli 调用超过 {timeout} 秒未返回（目标不可达或响应过慢）。",
            "argv": argv,
        }
    result.setdefault("argv", argv)
    return result
