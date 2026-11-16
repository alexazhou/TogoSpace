"""`mcp` 子树的 mcporter 风格 CLI（#291 T1–T3）。

按 `docs/design/2026-11-16-mcp-native-session/step1_product.md` §2 实现：

    mcp list [<server-name> | <server-name>.<tool-name>] [--schema|--brief|--all-parameters]
             [--stdio '<server-command>'] [--output json|text]
    mcp call <server-name>.<tool-name> [--args '<args-json>'] [<name>=<value> …] [--output json|text]
    mcp config list | get <server-name> | add <server-name> <target> | remove <server-name>
    mcp resource <server-name> [<resource-uri>]
    mcp session start | status | stop
    mcp enable <server-name> / mcp disable <server-name>   （既有注册表能力，非 mcporter 语法）

## 为什么是「局部专用解析器」而不扩通用 console 框架

通用 `consoleCommands` 的 spec 机制假定「一条命令 = 资源 + 动词 + 至多一个位置参数，选项面从
某个既有工具签名派生」。本子树需要三层子命令（`mcp config list`）、双位置参数
（`config add <name> <target>`、`resource <server> <uri>`）、`name=value` 具名参数与
`--output` 这类纯展示选项 —— 这些语义**只属于 mcp**。把它们塞进通用框架会让 34 条既有
命令的解析路径一起变（风险与收益不成比例），所以这里自带一个窄解析器，
`consoleTool.console()` 只在 `tokens[0] == "mcp"` 时短路进来。

## 两条入口的分工（Q1=A，产品 §5「OpenAPI / GraphQL 能力保留，CLI 另行明确」）

- `mcp list` / `mcp call <server>.<tool>` / `mcp resource` / `mcp session` ⇒ **MCP 原生路径**
  （Python `mcp` SDK，`mcpSessionService`）。
- `mcp ls <path/URL>` / `mcp call --kind openapi|graphql …` ⇒ **既有 mcp2cli 路径**，原样委托
  `tools.list_mcp_servers` / `tools.mcp_call`，行为与文案不变（它是 A 类不变量）。
  两条靠**命令词不同**（`list` vs `ls`）区分，不靠猜参数，所以无二义；`help` 里各给一条
  可复制示例，避免「能力保住了但用户不知道怎么用」。

## 错误反馈口径（产品 §4.1 第 3 条）

语法错误 / 未知选项 / 缺目标 / 缺参数一律：固定错误码 + 一句话原因 + **一行可直接复制的
固定占位符示例**，不动态拼装注册表实值。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from exception import TogoException
from service import mcpRegistryService as registry
from service import mcpSessionService as sessions

from . import consoleParsing as cp
from .consoleParsing import ConsoleError

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
EX_LEGACY_OPENAPI = "mcp ls <spec-path-or-url>   /   mcp call --kind openapi <spec-path-or-url> --tool <tool-name>"

#: 布尔开关（出现即 True，不吞下一个 token）。
_BOOL_FLAGS = frozenset({"schema", "brief", "all-parameters", "overwrite", "help"})
#: 需要值的选项（`--k v` 或 `--k=v`）。
_VALUE_FLAGS = frozenset(
    {
        "output",
        "stdio",
        "args",
        "protocol",
        "note",
        "config",
        "kind",
        "tool",
        "extra",
        "name",
        "target",
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
                options[key.replace("_", "-")] = value
                index += 1
                continue
            key = raw.replace("_", "-")
            if key in _BOOL_FLAGS:
                options[key] = True
                index += 1
                continue
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


def _content_text(result: Any) -> str:
    """`CallToolResult` ⇒ 文本（优先 text 段，其余按 JSON 序列化）。"""
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        data = getattr(item, "data", None)
        if data is not None:
            parts.append(str(data))
    return "\n".join(parts)


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
        # 无参：列已配置服务及其状态（`mcp config list` 是纯配置视角，两者都列配置）
        entries = await registry.list_servers()
        servers = [registry.entry_summary(entry) for entry in entries]
        body: dict[str, Any] = {"success": True, "count": len(servers)}
        if mode == "json":
            body["servers"] = servers
            return body
        lines = [f"已配置 {len(servers)} 个 server"]
        for summary in servers:
            state = "启用" if summary["enable"] else "已禁用"
            lines.append(
                f"  {summary['name']}  [{summary['kind']}/{summary['protocol'] or '-'}]  "
                f"{summary['target_masked']}  {state}"
            )
        lines.append(f"看某个 server 的工具：{EX_LIST}")
        lines.append(f"临时来源（MCP）：mcp list <server-url> --brief   /   {EX_LIST_STDIO}")
        lines.append(f"OpenAPI / GraphQL 直传（旧路径，语义不变）：{EX_LEGACY_OPENAPI}")
        body["text"] = "\n".join(lines)
        return body

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
        raise ConsoleError(
            _ERROR_MISSING_SCHEMA_FLAG,
            f"查看单个工具的 schema 需要 --schema：收到 {target!r} 但未带 --schema。"
            f"看该 server 的全部工具请用 `mcp list {name}`。正确调用：\n  {EX_LIST_SCHEMA}",
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


async def _cmd_ls(positional: list[str], options: dict[str, Any], context: Any) -> dict[str, Any]:
    """`mcp ls …`：**既有 openapi / graphql 直传路径**（Q1=A 保留，A 类不变量）。

    原样委托 `tools.list_mcp_servers`：不换错误码、不改字段、不动文案。
    """
    from . import tools as func_tools

    name_or_target = positional[0] if positional else None
    tool = options.get("tool")
    extra = _maybe_json_object(options.get("extra"), "--extra")
    kind = str(options.get("kind") or "openapi")
    return await func_tools.list_mcp_servers(
        name_or_target=name_or_target,
        tool=str(tool) if tool is not None else None,
        args=None,
        kind=kind,
        extra=extra,
        _context=context,
    )


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
        frozenset({"args", "output", "kind", "extra", "tool", "protocol", "config"}),
        EX_CALL,
    )
    mode = _output_mode(options)
    kind = options.get("kind")

    # 既有 openapi / graphql 直传（A 类不变量）：原样委托 `tools.mcp_call`
    if kind in ("openapi", "graphql"):
        from . import tools as func_tools

        if not positional:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                f"`mcp call --kind {kind}` 需要 target。正确调用：\n  {EX_LEGACY_OPENAPI}",
                example=EX_LEGACY_OPENAPI,
            )
        args = _maybe_json_object(options.get("args"), "--args")
        extra = _maybe_json_object(options.get("extra"), "--extra")
        return await func_tools.mcp_call(
            target=positional[0],
            action="call",
            tool=str(options["tool"]) if options.get("tool") is not None else None,
            args=args,
            kind=str(kind),
            extra=extra,
            _context=context,
        )

    if kind is not None and kind != "mcp":
        raise ConsoleError(
            _ERROR_UNKNOWN_OPTION,
            f"--kind 只支持 openapi / graphql（MCP 走 `<server-name>.<tool-name>` 点式定位，"
            f"不需要 --kind），收到 {kind!r}。",
            example=EX_CALL,
        )
    if not positional:
        raise ConsoleError(
            _ERROR_MISSING_TARGET,
            f"`mcp call` 缺少工具定位。正确调用：\n  {EX_CALL}",
            example=EX_CALL,
        )

    target = positional[0]
    if "." not in target:
        # 无点形态有两种合法来源，必须分开判（A 类不变量 vs B 类已删语法）：
        # ① target 命中注册表且登记 kind 是 openapi / graphql ⇒ **既有能力**，原样委托
        #    `tools.mcp_call`（注册表解析 + kind 继承 + `mcp_name_kind_conflict` 等校验
        #    全在它里面，一个字节都不该在这里重写）；
        # ② 其余（MCP kind 的注册项、未命中、URL 直传）⇒ 旧 `mcp call <名称> --tool`
        #    语法已随 T0 移除，给 `mcp_legacy_syntax_removed` + 新示例指路。
        entry = await registry.resolve_name(target)
        if entry is not None and entry.kind in ("openapi", "graphql"):
            from . import tools as func_tools

            return await func_tools.mcp_call(
                target=target,
                action="call",
                tool=str(options["tool"]) if options.get("tool") is not None else None,
                args=_maybe_json_object(options.get("args"), "--args"),
                kind=str(kind or entry.kind),
                extra=_maybe_json_object(options.get("extra"), "--extra"),
                _context=context,
            )
        raise ConsoleError(
            _ERROR_LEGACY_SYNTAX,
            f"`mcp call` 的工具定位必须写成 `<server-name>.<tool-name>`，收到 {target!r}；"
            f"旧写法（`mcp call <名称> --tool <工具名> --args …`）已移除。正确调用：\n  {EX_CALL}",
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
        body["message"] = _content_text(result)
        return body
    if mode == "json":
        body["content"] = [_jsonable(item) for item in getattr(result, "content", None) or []]
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            body["structured_content"] = structured
        return body
    body["text"] = _content_text(result)
    return body


# --------------------------------------------------------------------------- #
# 子命令：config
# --------------------------------------------------------------------------- #


async def _cmd_config(positional: list[str], options: dict[str, Any], context: Any) -> dict[str, Any]:
    action = positional[0].lower() if positional else ""
    rest = positional[1:]
    if action == "list":
        _reject_unknown_options(options, frozenset({"output"}), EX_CONFIG_LIST)
        entries = await registry.list_servers()
        servers = [registry.entry_summary(entry) for entry in entries]
        body: dict[str, Any] = {"success": True, "count": len(servers), "servers": servers}
        if _output_mode(options) == "text":
            lines = [f"已配置 {len(servers)} 个 server"]
            for summary in servers:
                lines.append(
                    f"  {summary['name']}  [{summary['kind']}/{summary['protocol'] or '-'}]  "
                    f"{summary['target_masked']}  {'启用' if summary['enable'] else '已禁用'}"
                )
            body["text"] = "\n".join(lines)
        return body
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
        return {"success": True, "server": registry.entry_summary(entry)}
    if action == "add":
        _reject_unknown_options(
            options,
            frozenset({"protocol", "note", "config", "overwrite", "output", "kind"}),
            EX_CONFIG_ADD,
        )
        if len(rest) < 2:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                f"`mcp config add` 需要名称与 target 两个位置参数。正确调用：\n  {EX_CONFIG_ADD}",
                example=EX_CONFIG_ADD,
            )
        name, target = rest[0], rest[1]
        protocol = str(options["protocol"]) if options.get("protocol") is not None else None
        if protocol is None:
            # 产品 §4.1：HTTP/SSE 按 URL 探测（缺省 streamable_http），stdio 命令则 stdio。
            protocol = "stdio" if not target.lower().startswith(("http://", "https://")) else None
        entry = await registry.save_server(
            name=name,
            kind="mcp",
            protocol=protocol,
            target=target,
            note=str(options.get("note") or ""),
            config=_maybe_json_object(options.get("config"), "--config"),
            overwrite=bool(options.get("overwrite")),
            created_by=int(getattr(context, "agent_id", 0) or 0),
        )
        return {
            "success": True,
            "server": registry.entry_summary(entry),
            "message": f"name {entry.name!r} 已登记；调用方式：mcp call {entry.name}.<tool-name> --args '{{...}}'",
        }
    if action == "remove":
        if not rest:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                "`mcp config remove` 需要 server 名称。正确调用：\n  mcp config remove <server-name>",
                example="mcp config remove <server-name>",
            )
        entry = await registry.delete_server(rest[0])
        return {"success": True, "removed": registry.entry_summary(entry)}
    if action in ("enable", "disable"):
        if not rest:
            raise ConsoleError(
                _ERROR_MISSING_TARGET,
                f"`mcp config {action}` 需要 server 名称。",
                example=f"mcp config {action} <server-name>",
            )
        entry = await registry.set_enabled(rest[0], action == "enable")
        return {"success": True, "server": registry.entry_summary(entry)}
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
            texts.append(str(text))
        else:
            blob = getattr(item, "blob", None)
            texts.append(f"<binary {len(blob) if blob else 0} bytes>")
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
            "的 server 连接会保留，`mcp session stop` 统一关闭。"
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
    """`mcp enable|disable`：既有注册表能力，**原样委托**底层工具。

    返回体逐字节沿用 `tools.set_mcp_server_enabled`（A 类不变量）—— 自己拼一份 message
    就等于给同一条命令造第二个返回体来源。
    """
    from . import tools as func_tools

    example = f"mcp {'enable' if enable else 'disable'} <server-name>"
    _reject_unknown_options(options, frozenset(), example)
    if not positional:
        raise ConsoleError(
            _ERROR_MISSING_TARGET, f"需要 server 名称。正确调用：\n  {example}", example=example
        )
    return await func_tools.set_mcp_server_enabled(
        name=positional[0], enable=enable, _context=context
    )


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
        "  mcp session start | status | stop                  开启 / 查看 / 关闭当前 Agent 的会话",
        "  ── 以下为既有能力，语义未变 ──",
        "  mcp ls <spec-path-or-url>                          OpenAPI / GraphQL 直传（mcp2cli 路径）",
        "  mcp call --kind openapi|graphql <target> --tool <name> --args '{...}'   同上",
        "  mcp enable <n> / mcp disable <n>                   启用 / 禁用已保存的服务（既有注册表能力）",
        "示例：" + EX_CALL,
        "详情：mcp <子命令> --help；示例占位符需替换为实际值（尖括号本身不是参数）。",
    ]
)

MCP_HELP_DETAIL: dict[str, str] = {
    "list": "\n".join(
        [
            "mcp list — 发现服务与工具（MCP 原生路径，SDK 直连，不是 mcp2cli）",
            f"  {EX_LIST}                       列该 server 的工具",
            f"  {EX_LIST_SCHEMA}  看单工具 schema（缺 --schema 会报错指路）",
            f"  {EX_LIST_STDIO}   临时 stdio 来源",
            "  mcp list <server-url> --brief     临时 HTTP 来源（未注册）",
            "  --brief 只列工具名；--all-parameters 连参数表一起列；--output json 给完整 JSON",
            "注意：`mcp list` 是 MCP 服务；OpenAPI / GraphQL 直传用 `mcp ls <spec-path-or-url>`（旧路径，语义不变）。",
        ]
    ),
    "call": "\n".join(
        [
            "mcp call — 调用一个工具",
            f"  {EX_CALL}",
            f"  {EX_CALL_NAMED}  参数类型按工具 inputSchema 转换（string 保留原文）",
            "  同名参数同时出现在 --args 与 name=value 时，以 name=value 为准",
            "  --output json 输出完整结构化结果；默认 text 只给内容文本",
            "  OpenAPI / GraphQL 直传（既有能力）：mcp call --kind openapi|graphql <target> --tool <name>",
        ]
    ),
    "config": "\n".join(
        [
            "mcp config — 管理已保存的服务定义（与设置页、HTTP 接口同一份全局注册数据）",
            f"  {EX_CONFIG_LIST}",
            "  mcp config get <server-name>",
            f"  {EX_CONFIG_ADD}   [--protocol stdio|sse|streamable_http] [--note …] [--config '{...}']",
            "  mcp config remove <server-name>",
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
    "ls": "\n".join(
        [
            "mcp ls — OpenAPI / GraphQL 直传发现（既有能力，mcp2cli 路径，本次语义未变）",
            "  mcp ls <spec-path-or-url> [--tool <name>] [--kind openapi|graphql]",
            "注意：MCP 服务请用 `mcp list`（SDK 直连、支持 session keepalive）；",
            "      `mcp ls` 走 mcp2cli，不参与 session keepalive。",
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
        return {"success": True, "text": MCP_HELP_SUMMARY, "help": "mcp"}
    detail = MCP_HELP_DETAIL.get(subject)
    if detail is None:
        return {
            "success": True,
            "text": MCP_HELP_SUMMARY,
            "help": "mcp",
            "note": f"没有 {subject!r} 的单独帮助，已给出总览。",
        }
    return {"success": True, "text": detail, "help": f"mcp {subject}"}


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
        if sub == "ls":
            return await _cmd_ls(rest, options, context)
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
            "未知的 mcp 子命令：" + sub + "。可用：list / call / config / resource / session / ls / enable / disable。",
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
