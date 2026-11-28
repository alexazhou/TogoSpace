"""`mcp` 子命令（`list` / `call` / `resource` / `session` / `config`）的解析与执行。

这套子命令需要三层子命令、两个位置参数和 `name=value` 形式的具名参数；通用链路（`consoleParsing._parse_options` + `consoleRegistry` 的命令表）假定一条命令只有一个位置参数、选项从工具签名派生，套不进去，硬塞进去 34 条既有命令的解析会跟着一起改，`mcp list`（原生）和 `mcp ls`（mcp2cli）两条入口也会变得分不清。

`consoleTool.console()` 只在 `tokens[0] == "mcp"` 时进本模块，解析自带（只从 `consoleParsing` 借 `HELP_FLAGS` 与 `_error_payload`），别去改那条通用链路；改完跑 `tests/unit/service/funcToolService/test_console_mcp_usability_281.py`。命令写法见 `docs/design/2026-11-16-mcp-native-session/step1_product.md` §2，为什么这么分层见 step2 §8。"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from exception import ForwardToolCall, TogoException
from service import mcpRegistryService as registry
from service import mcpSessionService as sessions

from . import consoleParsing as cp
from .consoleParsing import ConsoleError
# `_save_binary` 是 #300 已交付的落盘入口（内部即 `_persist()`：超限/扩展名/magic 校验/
# 写入/占位全在一处）。这里**只调用、不复制**：mcpContent.py 属已验收契约（#300），本单
# 一个字节都不改，避免出现第二条落盘实现与第二套占位口径。
from .mcpContent import _save_binary as _save_mcp_binary
from .mcpContent import render_content_text

# --------------------------------------------------------------------------- #
# 固定占位符示例（唯一实现：help、错误文案、报告实收都引用这里，不各抄一份）
# --------------------------------------------------------------------------- #

EX_LIST = "mcp list <server-name>"
EX_LIST_SCHEMA = "mcp list <server-name>.<tool-name> --schema"
EX_LIST_STDIO = "mcp list --stdio '<server-command>' --brief"
EX_CALL = "mcp call <server-name>.<tool-name> --args '{\"key\":\"value\"}'"
EX_CALL_NAMED = "mcp call <server-name>.<tool-name> <parameter-name>=<parameter-value>"
EX_CONFIG_LIST = "mcp config list"
EX_CONFIG_ADD = "mcp config add <server-name> <server-url>"
EX_RESOURCE = "mcp resource <server-name> <resource-uri>"
EX_SESSION = "mcp session start"


#: 布尔开关（出现即 True，不吞下一个 token）。
_BOOL_FLAGS = frozenset({"schema", "brief", "all-parameters", "overwrite", "help"})
#: 需要值的选项（`--k v` 或 `--k=v`）。
_VALUE_FLAGS = frozenset(
    {
        # 只列**当前子命令集合实际消费**的选项（need_fix_4 §七模块4.1）：`kind` / `tool` /
        # `extra` / `name` / `target` 是已移除的旧语法（`mcp ls <spec>` / `mcp call --tool`）
        # 留下的，留着它们只会让 `--kind x` 这类死选项"吃掉"后面的值，报错更难读。
        "output",
        "stdio",
        "args",
        "protocol",
        "note",
        "config",
    }
)

_ERROR_UNKNOWN_SUB = "mcp_unknown_subcommand"
_ERROR_UNKNOWN_OPTION = "unknown_option"
_ERROR_MISSING_TARGET = "mcp_missing_target"
_ERROR_MISSING_ARG = "mcp_missing_argument"
_ERROR_MISSING_SCHEMA_FLAG = "mcp_missing_schema_flag"
_ERROR_INVALID_ARGS = "mcp_invalid_args"
_ERROR_LEGACY_SYNTAX = "mcp_legacy_syntax_removed"
_ERROR_NOT_FOUND = "mcp_name_not_found"


def _error(code: str, message: str, *, example: str, **extra: Any) -> dict[str, Any]:
    """统一的失败返回体：错误码 + 原因 + 一行可复制示例（产品 §4.1 第 3 条）。"""
    body: dict[str, Any] = {
        "success": False,
        "error_code": code,
        "message": message,
        "example": example,
    }
    body.update(extra)
    return body


# --------------------------------------------------------------------------- #
# 窄解析器
# --------------------------------------------------------------------------- #


def _reject_duplicate_option(options: dict[str, Any], key: str) -> None:
    """标量选项重复出现 ⇒ `duplicate_option`（沿用 #218 的既有口径）。

    不静默取最后一个：`--kind mcp --kind openapi` 这种写法是误输入，后写覆盖会把
    「我本来想选 openapi」变成「实际走了 mcp」，而调用方看到的是一次成功。
    布尔开关（`--brief --brief`）重复无歧义，不走这条。
    """
    if key in options:
        raise ConsoleError(
            "duplicate_option",
            f"选项 --{key} 出现了多次，无法判断用哪个值。只写一次：\n  {EX_CALL}",
            example=EX_CALL,
        )


def parse_tokens(tokens: list[str]) -> tuple[list[str], dict[str, Any], dict[str, str]]:
    """把 mcp 子树的 token 切成 `(位置参数, 选项, 具名参数)`。

    - `--flag=value` ⇒ 选项；`--flag value` ⇒ 选项（flag 在 `_VALUE_FLAGS` 里才吞值）；
    - `--flag` 未知且后面跟非 `--` 值 ⇒ 仍按「吞值」处理（宽容），避免把值当位置参数；
    - `name=value`（不以 `-` 开头、含 `=`）⇒ 具名参数（仅 `call` 使用，产品 §4.1）；
    - 其余 ⇒ 位置参数。
    """
    positional: list[str] = []
    options: dict[str, Any] = {}
    named: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            raw = token[2:]
            if not raw:
                raise ConsoleError(_ERROR_UNKNOWN_OPTION, "选项名不能为空。", example=EX_LIST)
            if "=" in raw:
                key, value = raw.split("=", 1)
                key = key.replace("_", "-")
                _reject_duplicate_option(options, key)
                options[key] = value
                index += 1
                continue
            key = raw.replace("_", "-")
            if key in _BOOL_FLAGS:
                options[key] = True
                index += 1
                continue
            _reject_duplicate_option(options, key)
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                options[key] = tokens[index + 1]
                index += 2
                continue
            if key in _VALUE_FLAGS:
                raise ConsoleError(
                    _ERROR_UNKNOWN_OPTION,
                    f"选项 --{key} 需要一个值。",
                    example=EX_CALL,
                )
            options[key] = True
            index += 1
            continue
        if token.startswith("-") and len(token) == 2:
            # `-h` 由调用方在 help 短路里处理，这里当布尔 help 兜住。
            options["help"] = True
            index += 1
            continue
        if "=" in token and not token.startswith("-"):
            name, value = token.split("=", 1)
            if not name:
                raise ConsoleError(
                    _ERROR_INVALID_ARGS, f"具名参数缺名称：{token!r}。", example=EX_CALL_NAMED
                )
            named[name] = value
            index += 1
            continue
        positional.append(token)
        index += 1
    return positional, options, named


def _reject_unknown_options(options: dict[str, Any], allowed: frozenset[str], example: str) -> None:
    unknown = sorted(key for key in options if key not in allowed)
    if unknown:
        raise ConsoleError(
            _ERROR_UNKNOWN_OPTION,
            "未知选项：" + "、".join(f"--{key}" for key in unknown) + "。" + f"正确调用：\n  {example}",
            example=example,
        )


# --------------------------------------------------------------------------- #
# 输出格式化
# --------------------------------------------------------------------------- #


def _output_mode(options: dict[str, Any]) -> str:
    mode = str(options.get("output") or "text").strip().lower()
    if mode not in ("text", "json"):
        raise ConsoleError(
            _ERROR_UNKNOWN_OPTION,
            f"--output 只支持 text / json，收到 {mode!r}。",
            example=EX_CALL,
        )
    return mode


def _render_tools(server: str, tools: list[Any], *, brief: bool, full: bool) -> str:
    """工具清单的人读形态（`--brief` 只名字；`--all-parameters` 加参数表）。"""
    lines = [f"{server}：{len(tools)} 个工具"]
    for tool in tools:
        schema = getattr(tool, "inputSchema", None) or {}
        props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
        if brief:
            lines.append(f"  {tool.name}")
            continue
        description = (getattr(tool, "description", None) or "").strip().splitlines()
        head = f"  {tool.name}"
        if description:
            head += f"  — {description[0]}"
        lines.append(head)
        if full and props:
            for name, spec in props.items():
                kind = (spec or {}).get("type", "any") if isinstance(spec, dict) else "any"
                required = name in (schema.get("required") or [])
                lines.append(f"      {name}: {kind}{'' if required else '（可选）'}")
    if not brief:
        lines.append(f"调用：{EX_CALL}")
    return "\n".join(lines)


def _content_text(result: Any, tool_name: str = "") -> str:
    """`CallToolResult` ⇒ 文本（多模态段按 `content[].type` 分发，见 `mcpContent`）。

    改造前这里只做「有 `text` 取 text，否则把 `data` 拼进去」，`type` / `mimeType` 全丢
    ⇒ 截图类工具会把近 1MB 裸 base64 灌进上下文（need_fix_3 §2）。现在 image/audio 段落盘
    并返回路径占位，`tool_name` 只用于文件名溯源。
    """
    return render_content_text(
        getattr(result, "content", None) or [], tool_name=tool_name or "mcp"
    )


def _jsonable(value: Any) -> Any:
    """把 SDK 的类型对象转成可 JSON 序列化的结构（pydantic v2 `model_dump`）。"""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return value


# --------------------------------------------------------------------------- #
# 身份与连接
# --------------------------------------------------------------------------- #


def _identity(context: Any) -> tuple[int, int]:
    team_id = getattr(context, "team_id", None)
    agent_id = getattr(context, "agent_id", None)
    return sessions._require_identity(team_id, agent_id)


def _forward(
    tool_name: str,
    args: dict[str, Any],
    post_process: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
) -> Any:
    """交给既有工具执行（**必须**走 `ForwardToolCall`，不要直接 await 工具函数）。

    为什么不能直接调：`console` 的等价命令一直靠转发回到
    `AgentToolRegistry.execute_tool_call` 做**第二跳鉴权**（`enabled` = allow_specs、
    `effective_visibility.console_visible`、`admin_denied` 提示都只有那一条判定路径）。
    在子树里直接 `await tools.xxx(...)` 会把这些门控整段绕开 —— 只读 Agent 用新语法
    就能改 team 级注册表，这是权限放宽，不是实现细节。所以凡**有等价工具**的分支一律
    转发，只有 MCP 原生路径（无对应工具）才直连服务层。

    `None` 值不传：选项未给时保持工具的默认值，`argv` 之类的返回字段才与改前一致。
    """
    raise ForwardToolCall(
        tool_name,
        {key: value for key, value in args.items() if value is not None},
        post_process=post_process,
    )


async def _with_connection(
    context: Any, ref: sessions.ServerRef, action: str, **kwargs: Any
) -> Any:
    """借一条连接执行一次协议调用，用完按 lease 规则归还（临时的直接关）。"""
    team_id, agent_id = _identity(context)
    lease = await sessions.acquire_connection(team_id, agent_id, ref)
    try:
        return await lease.connection.invoke(action, **kwargs)
    finally:
        await lease.release()


def _is_http_url(target: str) -> bool:
    """`http://` / `https://` 前缀即视为 URL（与 `sessions.resolve_ref` 的判定口径一致）。"""
    return str(target or "").strip().lower().startswith(("http://", "https://"))


def _is_registry_name(text: str) -> bool:
    """是否可能是一个注册表 server 名（复用 `mcpRegistryService` 的唯一 name 规则）。"""
    try:
        registry.validate_name(text)
    except TogoException:
        return False
    return True


def _split_server_tool(target: str) -> tuple[str, str]:
    """`<server-name>.<tool-name>` ⇒ `(server, tool)`；按**第一个点**切。

    registry name 规则（`^[a-z][a-z0-9_-]{0,31}$`）不含点，所以第一个点之前必然是 server
    名；工具名里即使含点也整体归 tool。少一个点或空段都当语法错误（产品 §2.3 只定义了点式）。
    """
    name, sep, tool = target.partition(".")
    if not sep or not name.strip() or not tool.strip():
        raise ConsoleError(
            _ERROR_MISSING_TARGET,
            f"`mcp call` 的工具定位必须写成 `<server-name>.<tool-name>`，收到 {target!r}。"
            f"旧写法（`mcp call <名称> --tool <工具名>`）已移除。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        )
    return name.strip(), tool.strip()


# --------------------------------------------------------------------------- #
# 子命令：list / ls
# --------------------------------------------------------------------------- #


async def _cmd_list(positional: list[str], options: dict[str, Any], context: Any) -> dict[str, Any]:
    _reject_unknown_options(
        options,
        frozenset({"schema", "brief", "all-parameters", "stdio", "output", "protocol", "config"}),
        EX_LIST,
    )
    mode = _output_mode(options)
    stdio = options.get("stdio")
    target = positional[0] if positional else None

    # `--stdio '<命令>'` ⇒ 临时 stdio 来源（产品 §2.2）
    if stdio:
        if len(positional) > 1:
            raise ConsoleError(
                _ERROR_MISSING_ARG,
                f"`--stdio` 只接受一个完整启动命令，收到多余位置参数 {positional[1:]!r}。",
                example=EX_LIST_STDIO,
            )
        ref = await sessions.resolve_ref("", stdio_command=str(stdio))
        return await _list_from_ref(ref, options, mode, context)

    if target is None:
        # 无参：列已配置服务及其状态（`mcp config list` 是纯配置视角，两者都列配置）。
        # 转发给 `list_mcp_servers` 无参分支：列表来源与脱敏规则只保留一份实现。
        return _forward(
            "list_mcp_servers",
            {},
            post_process=_only_mcp_servers if mode == "json" else _render_list_overview,
        )

    # 临时 HTTP 来源（未注册）：**必须先于点式分派判断**。
    # 真实 URL 一定含点（`https://host/path`），若先判 `"." in target` 就会被
    # `<server-name>.<tool-name>` 分支吃掉，导致 help 里承诺的
    # `mcp list <server-url> --brief` 永远走不到下面的 `resolve_ref`（URL 分支成死代码）。
    if _is_http_url(target):
        ref = await sessions.resolve_ref(target, protocol=options.get("protocol"))
        return await _list_from_ref(ref, options, mode, context)

    # `<server-name>.<tool-name> --schema` ⇒ 单工具 schema（产品 §2.2）
    if "." in target:
        return await _list_one_tool_schema(target, options, mode, context)

    ref = await sessions.resolve_ref(target)
    return await _list_from_ref(ref, options, mode, context)


async def _list_one_tool_schema(
    target: str, options: dict[str, Any], mode: str, context: Any
) -> dict[str, Any]:
    name, tool_name = _split_server_tool(target)
    if not options.get("schema"):
        # 方案 A（产品 §2.2 只定义了点式 + `--schema` 的形态）：不猜，直接指路。
        # 指路必须指向**真实可用**的形态：只有点前那段确实是注册表名时才给
        # `mcp list <name>`；否则（典型是 URL 被误当点式）改给两选一指引 ——
        # 指错路的文案比不指路更坏。
        if _is_registry_name(name):
            hint = f"看该 server 的全部工具请用 `mcp list {name}`。"
        else:
            hint = (
                "若这是 URL，看它的全部工具请用 `mcp list <server-url> --brief`；"
                "若是单工具 schema，请写成 `<server-name>.<tool-name> --schema`。"
            )
        raise ConsoleError(
            _ERROR_MISSING_SCHEMA_FLAG,
            f"查看单个工具的 schema 需要 --schema：收到 {target!r} 但未带 --schema。"
            f"{hint}正确调用：\n  {EX_LIST_SCHEMA}",
            example=EX_LIST_SCHEMA,
        )
    ref = await sessions.resolve_ref(name)
    result = await _with_connection(context, ref, "list_tools")
    found = None
    for tool in getattr(result, "tools", None) or []:
        if tool.name == tool_name:
            found = tool
            break
    if found is None:
        raise ConsoleError(
            _ERROR_NOT_FOUND,
            f"{name} 没有名为 {tool_name!r} 的工具；`mcp list {name}` 可看全部工具名。",
            example=EX_LIST_SCHEMA,
        )
    schema = _jsonable(getattr(found, "inputSchema", None) or {})
    body: dict[str, Any] = {
        "success": True,
        "server": name,
        "tool": tool_name,
        "schema": schema,
    }
    if mode == "json":
        return body
    body["text"] = json.dumps(schema, ensure_ascii=False, indent=2)
    return body


async def _list_from_ref(
    ref: sessions.ServerRef, options: dict[str, Any], mode: str, context: Any
) -> dict[str, Any]:
    result = await _with_connection(context, ref, "list_tools")
    tools = list(getattr(result, "tools", None) or [])
    body: dict[str, Any] = {
        "success": True,
        "server": ref.display,
        "count": len(tools),
    }
    if mode == "json":
        body["tools"] = [_jsonable(tool) for tool in tools]
        return body
    body["text"] = _render_tools(
        ref.display,
        tools,
        brief=bool(options.get("brief")),
        full=bool(options.get("all-parameters")),
    )
    return body


# --------------------------------------------------------------------------- #
# 子命令：call
# --------------------------------------------------------------------------- #


def _maybe_json_object(raw: Any, flag: str) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    text = str(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConsoleError(
            _ERROR_INVALID_ARGS,
            f"{flag} 必须是 JSON 对象（以 `{{` 开头）：{exc.msg}。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        ) from None
    if not isinstance(parsed, dict):
        raise ConsoleError(
            _ERROR_INVALID_ARGS,
            f"{flag} 必须是 JSON 对象，收到 {type(parsed).__name__}。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        )
    return parsed


def _coerce_by_schema(value: str, spec: Any) -> Any:
    """按工具 `inputSchema` 的类型转换（产品 §4.1 第 2 条）。

    `string` **保留原文**（`a=1` 传 `1` 不变成数字）；其余类型按 JSON 解析，失败报错。
    """
    kind = (spec or {}).get("type") if isinstance(spec, dict) else None
    if kind in (None, "string"):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        raise ConsoleError(
            _ERROR_INVALID_ARGS,
            f"参数值 {value!r} 不是合法的 {kind}（按工具 schema 需要 {kind}）。"
            f"复杂值请用 --args '{{...}}'：\n  {EX_CALL}",
            example=EX_CALL,
        ) from None
    return parsed


async def _cmd_call(positional: list[str], options: dict[str, Any], context: Any) -> dict[str, Any]:
    _reject_unknown_options(
        options,
        frozenset({"args", "output", "protocol", "config"}),
        EX_CALL,
    )
    mode = _output_mode(options)
    if not positional:
        raise ConsoleError(
            _ERROR_MISSING_TARGET,
            f"`mcp call` 缺少工具定位。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        )

    target = positional[0]
    if "/" in target or "://" in target or "." not in target:
        raise ConsoleError(
            _ERROR_LEGACY_SYNTAX,
            f"`mcp call` 的工具定位必须写成 `<server-name>.<tool-name>`，收到 {target!r}；"
            f"OpenAPI / GraphQL 服务请分别使用 `openapi call` / `graphql call`。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        )
    server_name, tool_name = _split_server_tool(target)
    ref = await sessions.resolve_ref(server_name)

    explicit_args = _maybe_json_object(options.get("args"), "--args") or {}
    named_from_positional: dict[str, str] = {}
    for item in positional[1:]:
        if "=" not in item:
            raise ConsoleError(
                _ERROR_INVALID_ARGS,
                f"多余的位置参数 {item!r}：工具参数请用 `name=value` 或 `--args '{{...}}'`。"
                f"正确调用：\n  {EX_CALL_NAMED}",
                example=EX_CALL_NAMED,
            )
        key, _, value = item.partition("=")
        named_from_positional[key] = value

    # 拿一次工具清单：既校验工具存在，也用它的 inputSchema 做类型转换（产品 §4.1）。
    listed = await _with_connection(context, ref, "list_tools")
    tool_obj = None
    for tool in getattr(listed, "tools", None) or []:
        if tool.name == tool_name:
            tool_obj = tool
            break
    if tool_obj is None:
        raise ConsoleError(
            _ERROR_NOT_FOUND,
            f"{server_name} 没有名为 {tool_name!r} 的工具；`mcp list {server_name}` 可看全部工具名。",
            example=EX_CALL,
        )
    schema = getattr(tool_obj, "inputSchema", None) or {}
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}

    named_for_call: dict[str, Any] = {}
    for name, value in named_from_positional.items():
        named_for_call[name] = _coerce_by_schema(value, props.get(name))
    # 同一参数两种来源（`--args` 与 `k=v`）同时给 ⇒ 不静默取一个：
    # 「同参数两处来源」在改前是 `ambiguous_input_source`（通用解析器规则），
    # 这里保持同一原则，只把它缩到真正重叠的键上（不重叠时两者是互补，不是歧义）。
    overlap = sorted(set(explicit_args) & set(named_for_call))
    if overlap:
        raise ConsoleError(
            "ambiguous_input_source",
            f"参数 {'、'.join(overlap)} 同时出现在 --args 与具名参数里，无法判断用哪个。"
            f"只保留一处：\n  {EX_CALL}",
            example=EX_CALL,
        )
    merged = dict(explicit_args)
    merged.update(named_for_call)

    missing = [
        name
        for name in (schema.get("required") or [])
        if name not in merged
    ] if isinstance(schema, dict) else []
    if missing:
        raise ConsoleError(
            _ERROR_MISSING_ARG,
            f"{server_name}.{tool_name} 缺少必填参数：{'、'.join(missing)}。"
            f"`mcp list {server_name}.{tool_name} --schema` 可看参数定义。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        )

    result = await _with_connection(context, ref, "call_tool", name=tool_name, arguments=merged)
    is_error = bool(getattr(result, "isError", False))
    body: dict[str, Any] = {
        "success": not is_error,
        "server": server_name,
        "tool": tool_name,
        "is_error": is_error,
    }
    if is_error:
        body["error_code"] = "mcp_tool_error"
        body["message"] = _content_text(result, tool_name)
        return body
    if mode == "json":
        body["content"] = [_jsonable(item) for item in getattr(result, "content", None) or []]
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            body["structured_content"] = structured
        return body
    body["text"] = _content_text(result, tool_name)
    return body


# --------------------------------------------------------------------------- #
# 子命令：config
# --------------------------------------------------------------------------- #


def _only_mcp_servers(body: dict[str, Any]) -> dict[str, Any]:
    """只保留 `kind == "mcp"` 的登记项——`mcp` 子树的列表口径与 openapi/graphql 对齐。

    need_fix_4 §七模块4.2 / B7：三条子树的 list 各管一种 kind，但 `mcp list` 与
    `mcp config list` 原本把 openapi/graphql 的登记也一起列了，模型看到 `mcp list`
    里有 openapi 项，就会拿它去敲 `mcp call <名称>`，然后被当成 MCP server 连接失败。
    过滤后**不静默**：被挡掉的条数写进 `note` 并指路到对应子命令。
    """
    servers = body.get("servers")
    if not isinstance(servers, list):
        return body
    kept = [item for item in servers if str(item.get("kind") or "").lower() == "mcp"]
    dropped = len(servers) - len(kept)
    body["servers"] = kept
    if "count" in body:
        body["count"] = len(kept)
    if dropped:
        body["note"] = (
            f"另有 {dropped} 个非 mcp 登记项未列出；OpenAPI / GraphQL 服务请分别用 "
            "`openapi config list` / `graphql config list` 查看。"
        )
    return body


def _render_list_overview(body: dict[str, Any]) -> dict[str, Any]:
    """`mcp list` 无参的文本形态：配置概览 + 两条入口路标（`tools.py` 返回体不动）。"""
    body = _only_mcp_servers(body)
    servers = body.get("servers") or []
    lines = [f"已配置 {len(servers)} 个 MCP server"]
    for summary in servers:
        state = "启用" if summary["enable"] else "已禁用"
        lines.append(
            f"  {summary['name']}  [{summary['kind']}/{summary['protocol'] or '-'}]  "
            f"{summary['target_masked']}  {state}"
        )
    lines.append(f"看某个 server 的工具：{EX_LIST}")
    lines.append(f"临时来源（MCP）：mcp list <server-url> --brief   /   {EX_LIST_STDIO}")
    lines.append(
        "OpenAPI / GraphQL 服务是独立命令，不在这份列表里："
        "openapi list <spec-or-name> / graphql list <url-or-name>"
    )
    body["text"] = "\n".join(lines)
    return body


def _render_config_list(body: dict[str, Any]) -> dict[str, Any]:
    """`mcp config list --output text` 的人读渲染（在工具返回体之后追加，不改原字段）。"""
    body = _only_mcp_servers(body)
    servers = body.get("servers") or []
    lines = [f"已配置 {len(servers)} 个 MCP server"]
    for summary in servers:
        lines.append(
            f"  {summary['name']}  [{summary['kind']}/{summary['protocol'] or '-'}]  "
            f"{summary['target_masked']}  {'启用' if summary['enable'] else '已禁用'}"
        )
    lines.append(f"看某个 server 的工具：{EX_LIST}")
    note = body.get("note")
    if note:
        lines.append(str(note))
    body["text"] = "\n".join(lines)
    return body


def _render_config_add(body: dict[str, Any]) -> dict[str, Any]:
    """`mcp config add` 成功后追加一句「怎么调用」（不改工具原字段）。"""
    server = body.get("server") or {}
    name = server.get("name")
    if name:
        body["message"] = (
            f"name {name!r} 已登记；调用方式：mcp call {name}.<tool-name> --args '{{...}}'"
        )
    return body


async def _cmd_config(positional: list[str], options: dict[str, Any], context: Any) -> dict[str, Any]:
    action = positional[0].lower() if positional else ""
    rest = positional[1:]
    if action == "list":
        _reject_unknown_options(options, frozenset({"output"}), EX_CONFIG_LIST)
        if len(rest) > 0:
            raise ConsoleError(
                "invalid_argument_count",
                f"`mcp config list` 不接受位置参数，收到 {rest!r}。看单个 server 用 mcp config get。"
                f"正确调用：\n  {EX_CONFIG_LIST}",
                example=EX_CONFIG_LIST,
            )
        text_mode = _output_mode(options) == "text"
        return _forward(
            "list_mcp_servers",
            {},
            post_process=_only_mcp_servers if not text_mode else _render_config_list,
        )
    if action == "get":
        if not rest:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                f"`mcp config get` 需要 server 名称。正确调用：\n  mcp config get <server-name>",
                example="mcp config get <server-name>",
            )
        entry = await registry.resolve_name(rest[0])
        if entry is None:
            raise ConsoleError(
                _ERROR_NOT_FOUND,
                f"没有名为 {rest[0]!r} 的已配置 server；{EX_CONFIG_LIST} 可看全部。",
                example=EX_CONFIG_LIST,
            )
        entry_kind = str(getattr(entry, "kind", "") or "").lower()
        if entry_kind != "mcp":
            # B7：`mcp config get <openapi 名>` 原本照常返回一条 openapi 配置，让
            # "mcp 子树只管 mcp"这条口径在 get 上漏掉。这里不返回配置正文，只说清归属。
            raise ConsoleError(
                "mcp_kind_mismatch",
                f"{rest[0]!r} 属于 {entry_kind}，不是 mcp；请改用 "
                f"`{entry_kind} config get {rest[0]}`。",
                example=f"{entry_kind} config get {rest[0]}",
            )
        return {"success": True, "server": registry.entry_summary(entry)}
    if action == "add":
        _reject_unknown_options(
            options,
            frozenset({"protocol", "note", "config", "overwrite", "output"}),
            EX_CONFIG_ADD,
        )
        if len(rest) < 2:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                f"`mcp config add` 需要名称与 target 两个位置参数。正确调用：\n  {EX_CONFIG_ADD}",
                example=EX_CONFIG_ADD,
            )
        if len(rest) > 2:
            raise ConsoleError(
                "invalid_argument_count",
                f"`mcp config add` 只接受名称与 target 两个位置参数，收到 {len(rest)} 个：{rest!r}。"
                f"正确调用：\n  {EX_CONFIG_ADD}",
                example=EX_CONFIG_ADD,
            )
        name, target = rest[0], rest[1]
        kind = "mcp"
        protocol = str(options["protocol"]) if options.get("protocol") is not None else None
        if protocol is None:
            # 产品 §4.1：HTTP/SSE 按 URL 探测（缺省 streamable_http），stdio 命令则 stdio。
            protocol = "stdio" if not target.lower().startswith(("http://", "https://")) else None
        # 写注册表转发回 `save_mcp_server`：Write 权限、`admin_denied` 提示、全局唯一
        # name 校验都在工具里，子树不重写一份。
        return _forward(
            "save_mcp_server",
            {
                "name": name,
                "kind": kind,
                "protocol": protocol,
                "target": target,
                "note": str(options.get("note") or ""),
                "config": _maybe_json_object(options.get("config"), "--config"),
                "overwrite": bool(options.get("overwrite")) or None,
            },
            post_process=_render_config_add,
        )
    if action == "remove":
        _reject_unknown_options(options, frozenset({"output"}), "mcp config remove <server-name>")
        if not rest:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                "`mcp config remove` 需要 server 名称。正确调用：\n  mcp config remove <server-name>",
                example="mcp config remove <server-name>",
            )
        if len(rest) > 1:
            raise ConsoleError(
                "invalid_argument_count",
                f"`mcp config remove` 只接受一个 server 名称，收到 {len(rest)} 个：{rest!r}。",
                example="mcp config remove <server-name>",
            )
        return _forward("delete_mcp_server", {"name": rest[0]})
    if action in ("enable", "disable"):
        _reject_unknown_options(options, frozenset(), f"mcp config {action} <server-name>")
        if not rest:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                f"`mcp config {action}` 需要 server 名称。",
                example=f"mcp config {action} <server-name>",
            )
        return _forward(
            "set_mcp_server_enabled", {"name": rest[0], "enable": action == "enable"}
        )
    raise ConsoleError(
        _ERROR_UNKNOWN_SUB,
        f"`mcp config` 的子命令只有 list / get / add / remove（enable / disable 亦可），"
        f"收到 {action!r}。正确调用：\n  {EX_CONFIG_LIST}",
        example=EX_CONFIG_LIST,
    )


# --------------------------------------------------------------------------- #
# 子命令：resource
# --------------------------------------------------------------------------- #


async def _cmd_resource(
    positional: list[str], options: dict[str, Any], context: Any
) -> dict[str, Any]:
    _reject_unknown_options(options, frozenset({"output", "protocol"}), EX_RESOURCE)
    mode = _output_mode(options)
    if not positional:
        raise ConsoleError(
            _ERROR_MISSING_TARGET,
            f"`mcp resource` 需要 server 名称。正确调用：\n  mcp resource <server-name>",
            example="mcp resource <server-name>",
        )
    ref = await sessions.resolve_ref(positional[0])
    if len(positional) == 1:
        result = await _with_connection(context, ref, "list_resources")
        resources = list(getattr(result, "resources", None) or [])
        body: dict[str, Any] = {"success": True, "server": ref.display, "count": len(resources)}
        if mode == "json":
            body["resources"] = [_jsonable(item) for item in resources]
            return body
        lines = [f"{ref.display}：{len(resources)} 个资源"]
        for item in resources:
            lines.append(f"  {item.uri}  {getattr(item, 'name', '') or ''}".rstrip())
        lines.append(f"读取：{EX_RESOURCE}")
        body["text"] = "\n".join(lines)
        return body
    uri = positional[1]
    result = await _with_connection(context, ref, "read_resource", uri=uri)
    contents = list(getattr(result, "contents", None) or [])
    body = {"success": True, "server": ref.display, "uri": uri, "count": len(contents)}
    if mode == "json":
        body["contents"] = [_jsonable(item) for item in contents]
        return body
    texts = []
    for item in contents:
        text = getattr(item, "text", None)
        if text is not None:
            # 文本资源：原文照出，逐字节不变（B66 明确要求）。
            texts.append(str(text))
            continue
        blob = getattr(item, "blob", None)
        if not blob:
            # 既无 text 又无 blob：协议上不该出现，保持改前的占位口径。
            texts.append("<binary 0 bytes>")
            continue
        # 二进制资源（B66）：落盘 + 一行事实型提示。
        # 改前这里是 `<binary N bytes>` —— 只有长度、内容既看不见也拿不到，等于白读一次。
        # 落盘复用 #300 的 `_save_binary`（内部 `_persist`）：同一 temp 目录、同一
        # `mcp-{name}-{uuid8}.{ext}` 命名、同一 mimeType→扩展名映射，没有第二套实现。
        # 提示只陈述事实（有文件、在哪、多大、什么类型），不写使用建议（Operator 23:29）。
        mime_type = str(getattr(item, "mimeType", "") or "")
        texts.append(_save_mcp_binary(str(blob), mime_type, ref.display, "资源"))
    body["text"] = "\n".join(texts)
    return body


# --------------------------------------------------------------------------- #
# 子命令：session（T3，无名称、按 Agent 隔离）
# --------------------------------------------------------------------------- #


async def _cmd_session(positional: list[str], options: dict[str, Any], context: Any) -> dict[str, Any]:
    _reject_unknown_options(options, frozenset({"output"}), EX_SESSION)
    action = positional[0].lower() if positional else ""
    if len(positional) > 1:
        raise ConsoleError(
            _ERROR_INVALID_ARGS,
            "`mcp session` 不接受名称或 server 参数（无名称、作用域恒为当前 Agent）。"
            f"正确调用：\n  {EX_SESSION}",
            example=EX_SESSION,
        )
    team_id, agent_id = _identity(context)
    if action == "start":
        status = await sessions.start_session(team_id, agent_id)
        status["message"] = (
            "session 已开启（未访问 server 时连接数为 0）；之后 mcp list / call / resource 命中"
            "的 server 连接会跨调用保留（适合连续操作浏览器等有状态服务）。"
            f"示例：{EX_CALL}；结束后执行 `mcp session stop` 关闭。"
        )
        return status
    if action == "status":
        status = await sessions.session_status(team_id, agent_id)
        if not status["active"]:
            status["message"] = "当前 Agent 没有活动 session（`mcp session start` 开启）。"
        return status
    if action == "stop":
        status = await sessions.stop_session(team_id, agent_id)
        status["message"] = "session 已关闭（未开启时也返回成功）；后续访问恢复一次性连接。"
        return status
    raise ConsoleError(
        _ERROR_UNKNOWN_SUB,
        f"`mcp session` 的子命令只有 start / status / stop，收到 {action!r}。正确调用：\n  {EX_SESSION}",
        example=EX_SESSION,
    )


# --------------------------------------------------------------------------- #
# 子命令：enable / disable（既有注册表能力，原地保留）
# --------------------------------------------------------------------------- #


async def _cmd_enable(
    positional: list[str], options: dict[str, Any], context: Any, enable: bool
) -> dict[str, Any]:
    """`mcp enable|disable`：既有注册表能力，**转发**给底层工具。

    返回体逐字节沿用 `tools.set_mcp_server_enabled`（A 类不变量）—— 自己拼一份 message
    就等于给同一条命令造第二个返回体来源。转发而不是直调：Write 权限与 `admin_denied`
    提示只在第二跳判定里，直调会把它们绕开。
    """
    example = f"mcp {'enable' if enable else 'disable'} <server-name>"
    _reject_unknown_options(options, frozenset(), example)
    if not positional:
        raise ConsoleError(
            _ERROR_MISSING_TARGET, f"需要 server 名称。正确调用：\n  {example}", example=example
        )
    if len(positional) > 1:
        raise ConsoleError(
            "invalid_argument_count",
            f"`{example.split(' ')[0]} {example.split(' ')[1]}` 只接受一个 server 名称，"
            f"收到 {len(positional)} 个：{positional!r}。正确调用：\n  {example}",
            example=example,
        )
    return _forward("set_mcp_server_enabled", {"name": positional[0], "enable": enable})


# --------------------------------------------------------------------------- #
# help
# --------------------------------------------------------------------------- #

MCP_HELP_SUMMARY = "\n".join(
    [
        "mcp — MCP 服务：发现工具、调用、管理配置、维护会话（session 为本项目扩展）",
        "  mcp list [" + "<server-name>" + "] [--brief|--all-parameters]     列工具（MCP 原生，SDK 直连）",
        "  mcp list <server-name>.<tool-name> --schema        看单个工具的完整 schema",
        "  mcp list <server-url> --brief  |  mcp list --stdio '<server-command>'   临时来源（未注册）",
        "  mcp call <server-name>.<tool-name> --args '{\"key\":\"value\"}'          调用工具（也可 name=value）",
        "  mcp config list | get <n> | add <n> <target> | remove <n>               管理已保存的服务定义",
        "  mcp resource <server-name> [<resource-uri>]        列资源 / 读资源",
        "  mcp session start | status | stop                  开启 / 查看 / 关闭当前 Agent 的持久化会话（连续操作浏览器等有状态 server 前先 start，连接跨调用保留）",
        "  mcp enable <n> / mcp disable <n>                   启用 / 禁用已保存的服务",
        "  （OpenAPI / GraphQL 服务请分别使用独立的 `openapi` / `graphql` 命令）",
        "示例：" + EX_CALL,
        "详情：mcp <子命令> --help；示例占位符需替换为实际值（尖括号本身不是参数）。",
    ]
)

MCP_HELP_DETAIL: dict[str, str] = {
    "list": "\n".join(
        [
            "mcp list — 发现服务与工具（MCP 原生路径，SDK 直连）",
            f"  {EX_LIST}                       列该 server 的工具",
            f"  {EX_LIST_SCHEMA}  看单工具 schema（缺 --schema 会报错指路）",
            f"  {EX_LIST_STDIO}   临时 stdio 来源",
            "  mcp list <server-url> --brief     临时 HTTP 来源（未注册）",
            "  --brief 只列工具名；--all-parameters 连参数表一起列；--output json 给完整 JSON",
            "注意：OpenAPI / GraphQL 服务请分别使用 `openapi list` / `graphql list`。",
        ]
    ),
    "call": "\n".join(
        [
            "mcp call — 调用一个工具",
            f"  {EX_CALL}",
            f"  {EX_CALL_NAMED}  参数类型按工具 inputSchema 转换（string 保留原文）",
            "  同一参数同时写在 --args 与 name=value 里会报 ambiguous_input_source（不静默取一个）",
            "  --output json 输出完整结构化结果；默认 text 只给内容文本",
            "  默认是单次调用：每次 `mcp call` 都现建连接、调用完即关闭该 server 连接，服务不常驻。",
            "  需要该 MCP 服务持续存在（连续操作浏览器等有状态 server）时，先执行 "
            "`mcp session start`，连接才跨调用保留。",
            "  OpenAPI / GraphQL 服务请分别使用 `openapi call` / `graphql call`。",
        ]
    ),
    "config": "\n".join(
        [
            "mcp config — 管理已保存的 MCP 服务定义（登记到 team 级注册表，**非创建业务实体**）",
            f"  {EX_CONFIG_LIST}",
            "  mcp config get <server-name>",
            f"  {EX_CONFIG_ADD}   [--protocol stdio|sse|streamable_http] [--note …] [--config '{...}']",
            "  mcp config remove <server-name>",
            "  mcp config enable|disable <server-name>   启用/禁用（同 mcp enable|disable）",
            "kind 默认 mcp（仅登记 MCP 服务；OpenAPI / GraphQL 请用 `openapi config` / `graphql config`）。",
            "配置管理不建立 MCP 连接；凭证在返回体里一律打码。",
        ]
    ),
    "resource": "\n".join(
        [
            "mcp resource — MCP server 发布的可读资料（说明文档、文件、配置、日志等）",
            "  mcp resource <server-name>                  列资源与 URI",
            f"  {EX_RESOURCE}                              读取内容",
            "以 server 提供 Resources 为前提；工具清单用 mcp list，执行工具用 mcp call。",
        ]
    ),
    "session": "\n".join(
        [
            "mcp session — 当前 Agent 的一段持续工作（本项目扩展，非 mcporter 语法）",
            "  mcp session start     开启；已开启则保持原 session 与已有连接，不重置",
            "  mcp session status    查看是否开启与保留的连接；未开启也正常返回",
            "  mcp session stop      关闭全部连接并结束 session；未开启时也算成功",
            "三条命令都不接受 session 名称或 server 参数；每个 Agent 同时最多一个 session；",
            "会话与连接按 Agent 隔离，其他 Agent 的操作互不影响；turn 结束/跨房间/正常休眠不自动关闭。",
        ]
    ),
    "enable": "mcp enable <server-name> — 启用（既有注册表能力）。禁用≠删除：`mcp config list` 仍列出。",
    "disable": "mcp disable <server-name> — 禁用（既有注册表能力）。活动 session 里该 server 的连接会被关闭。",
}


def help_payload(tokens: list[str]) -> dict[str, Any]:
    """`mcp --help` / `mcp <子命令> --help` / `help mcp …` 的统一出口。"""
    subject = ""
    for token in tokens:
        if token.startswith("-"):
            continue
        if token.lower() == "mcp":
            continue
        subject = token.lower()
        break
    if not subject:
        return {"success": True, "text": MCP_HELP_SUMMARY}
    detail = MCP_HELP_DETAIL.get(subject)
    if detail is None:
        return {
            "success": True,
            "text": MCP_HELP_SUMMARY,
            "note": f"没有 {subject!r} 的单独帮助，已给出总览。",
        }
    return {"success": True, "text": detail}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def dispatch(command: str, tokens: list[str], context: Any) -> dict[str, Any]:
    """`consoleTool.console()` 在 `tokens[0] == "mcp"` 时短路到本函数。"""
    try:
        positional, options, named = parse_tokens(tokens[1:])
        if options.get("help") or any(token in cp.HELP_FLAGS for token in tokens[1:]):
            return help_payload(tokens)
        sub = positional[0].lower() if positional else ""
        rest = positional[1:]
        if not sub:
            return help_payload(tokens)
        if sub == "help":
            return help_payload(["mcp"] + rest)
        if named and sub != "call":
            raise ConsoleError(
                _ERROR_INVALID_ARGS,
                f"只有 `mcp call` 接受 `name=value` 具名参数，`mcp {sub}` 不接受 {sorted(named)!r}。",
                example=EX_CALL,
            )
        if sub == "list":
            return await _cmd_list(rest, options, context)
        if sub == "call":
            if named:
                # 具名参数回到位置参数序列，交给 `_cmd_call` 统一校验/转换
                rest = rest + [f"{key}={value}" for key, value in named.items()]
            return await _cmd_call(rest, options, context)
        if sub == "config":
            return await _cmd_config(rest, options, context)
        if sub == "resource":
            return await _cmd_resource(rest, options, context)
        if sub == "session":
            return await _cmd_session(rest, options, context)
        if sub in ("enable", "disable"):
            return await _cmd_enable(rest, options, context, sub == "enable")
        raise ConsoleError(
            _ERROR_UNKNOWN_SUB,
            "未知的 mcp 子命令：" + sub + "。可用：list / call / config / resource / session / enable / disable（OpenAPI / GraphQL 请用 `openapi` / `graphql` 命令）。",
            example=EX_LIST,
        )
    except ConsoleError as exc:
        return cp._error_payload(exc)
    except TogoException as exc:
        # service / registry 层的业务错误：码与文案原样透传，不二次包装
        return {
            "success": False,
            "error_code": getattr(exc, "error_code", "mcp_error"),
            "message": str(exc),
        }
