"""`openapi` / `graphql` 两个独立 `console` 命令的共享解析器。

把 OpenAPI 与 GraphQL 从 `mcp` 子树彻底拆出：
- `openapi list|call|config|enable|disable` → 固定 `kind="openapi"`，调用转发 `openapi_call`
- `graphql list|call|config|enable|disable` → 固定 `kind="graphql"`，调用转发 `graphql_call`

所有写库、鉴权与执行仍通过 `ForwardToolCall` 走 `AgentToolRegistry.execute_tool_call` 第 2 跳，
不绕过权限门控。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from exception import ForwardToolCall, TogoException
from service import mcpRegistryService as registry

from . import consoleParsing as cp
from .consoleParsing import ConsoleError


_BOOL_FLAGS = frozenset({"overwrite", "help"})
_VALUE_FLAGS = frozenset({"args", "extra", "tool", "note", "config", "target", "output"})

_ERROR_UNKNOWN_SUB = "unknown_subcommand"
_ERROR_UNKNOWN_OPTION = "unknown_option"
_ERROR_MISSING_TARGET = "missing_target"
_ERROR_NOT_FOUND = "not_found"
_ERROR_INVALID_ARGS = "invalid_arguments"


def _forward(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    post_process: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """转发出 `openapi` / `graphql` 子树。

    **`None` 值不传** —— 与 `consoleMcp._forward` 同一口径：选项未给时保持工具默认值。
    不剥 None 会踩到 `save_mcp_server(overwrite: bool = False)` 这类非 Optional 形参：
    `openapi config add <name> <target>`（不带 `--overwrite`）会把 `overwrite=None` 传下去，
    被参数校验判成 `invalid_tool_args`（「应为 boolean，实际收到 None」）⇒ 命令直接不可用。
    """
    raise ForwardToolCall(
        tool_name,
        {key: value for key, value in tool_args.items() if value is not None},
        post_process=post_process,
    )


def _reject_unknown_options(options: dict[str, Any], allowed: frozenset[str], example: str) -> None:
    unknown = sorted(set(options) - allowed)
    if unknown:
        flags = ", ".join(f"--{key}" for key in unknown)
        raise ConsoleError(
            _ERROR_UNKNOWN_OPTION,
            f"未知选项 {flags}。正确调用：\n  {example}",
            example=example,
        )


def _maybe_json_object(raw: Any, flag: str, example: str) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ConsoleError(
            _ERROR_INVALID_ARGS,
            f"{flag} 必须是 JSON 对象（以 `{{` 开头）：{exc.msg}。正确调用：\n  {example}",
            example=example,
        ) from None
    if not isinstance(parsed, dict):
        raise ConsoleError(
            _ERROR_INVALID_ARGS,
            f"{flag} 必须是 JSON 对象，收到 {type(parsed).__name__}。正确调用：\n  {example}",
            example=example,
        )
    return parsed


def _parse_tokens(tokens: list[str]) -> tuple[list[str], dict[str, Any]]:
    positional: list[str] = []
    options: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in cp.HELP_FLAGS:
            options["help"] = True
            i += 1
            continue
        if token.startswith("--") and len(token) > 2:
            body = token[2:]
            if "=" in body:
                key, _, value = body.partition("=")
                if key not in _VALUE_FLAGS and key not in _BOOL_FLAGS:
                    raise ConsoleError(_ERROR_UNKNOWN_OPTION, f"未知选项 --{key}。")
                options[key] = value
                i += 1
                continue
            if body in _BOOL_FLAGS:
                options[body] = True
                i += 1
                continue
            if body in _VALUE_FLAGS:
                if i + 1 >= len(tokens):
                    raise ConsoleError(_ERROR_INVALID_ARGS, f"选项 --{body} 缺少值。")
                options[body] = tokens[i + 1]
                i += 2
                continue
            raise ConsoleError(_ERROR_UNKNOWN_OPTION, f"未知选项 --{body}。")
        positional.append(token)
        i += 1
    return positional, options


def _kind_hint(kind: str) -> str:
    """`<kind> list` 无参时的下一步提示（need_fix_4 §七模块4.2 / B6）。

    原先这里原样透传工具给的 `hint`，而那条 hint 是 **mcp** 的口径
    （`mcp list <名称>` / `mcp ls <spec>`），在 openapi/graphql 的返回体里指向的
    全是别的子树的命令。现在按当前子树生成，且只出现本子树真实存在的写法。
    """
    if kind == "openapi":
        return (
            "看某个 OpenAPI 服务的操作：`openapi list <spec-path-or-name>`；"
            "登记新服务：`openapi config add <name> <spec-path-or-url>`。"
            "MCP server 请用 `mcp list`。"
        )
    return (
        "看某个 GraphQL 服务的操作：`graphql list <endpoint-url-or-name>`；"
        "登记新服务：`graphql config add <name> <endpoint-url>`。"
        "MCP server 请用 `mcp list`。"
    )


def _filter_servers_by_kind(kind: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _post(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("success"):
            return result
        servers = [
            item for item in (result.get("servers") or [])
            if item.get("kind") == kind
        ]
        updated = dict(result)
        updated["servers"] = servers
        updated["count"] = len(servers)
        # 工具层的 hint 是 mcp 口径（`mcp list <名称>` / `mcp ls <spec>`），在本子树里
        # 指向死语法；换成当前 kind 的下一步。
        updated["hint"] = _kind_hint(kind)
        return updated

    return _post


def _examples(cmd: str) -> dict[str, str]:
    target_placeholder = "<spec-path-or-url>" if cmd == "openapi" else "<endpoint-url>"
    return {
        "list": f"{cmd} list {target_placeholder}",
        "call": f"{cmd} call {target_placeholder} --tool <tool-name> --args '{{\"key\":\"value\"}}'",
        "config_list": f"{cmd} config list",
        "config_add": f"{cmd} config add <name> {target_placeholder}",
        "enable": f"{cmd} enable <name>",
    }


def help_payload(tokens: list[str]) -> dict[str, Any]:
    cmd = tokens[0].lower() if tokens and tokens[0].lower() in ("openapi", "graphql") else "openapi"
    ex = _examples(cmd)
    label = "OpenAPI 服务" if cmd == "openapi" else "GraphQL 服务"
    lines = [
        f"{cmd} — {label}：发现操作、调用、管理已保存配置",
        f"  {ex['list']}                 列出可用操作（无参列已保存配置）",
        f"  {ex['call']}   调用指定操作",
        f"  {cmd} config list | get <n> | add <n> <target> | remove <n>     管理配置",
        f"  {cmd} enable <n> / {cmd} disable <n>                             启用 / 禁用",
        f"示例：{ex['call']}",
    ]
    if cmd == "openapi":
        lines.append(
            "注意：OpenAPI spec 里若没有 servers，base path 会丢，运行时不会报错——需要补 servers 或改用完整 target。"
        )
    summary = "\n".join(lines)
    return {"success": True, "text": summary}


async def dispatch(command: str, tokens: list[str], context: Any) -> dict[str, Any]:
    cmd = tokens[0].lower()
    call_tool = "openapi_call" if cmd == "openapi" else "graphql_call"
    ex = _examples(cmd)

    try:
        positional, options = _parse_tokens(tokens[1:])
        if options.get("help") or not positional:
            return help_payload([cmd])
        sub = positional[0].lower()
        rest = positional[1:]

        if sub == "help":
            return help_payload([cmd])

        if sub == "list":
            _reject_unknown_options(options, frozenset({"tool", "args", "extra", "target"}), ex["list"])
            target_opt = options.get("target")
            if target_opt is not None and rest:
                raise ConsoleError(
                    "ambiguous_input_source",
                    f"target 同时出现在位置参数（{rest[0]!r}）与 --target 里，无法判断用哪个。\n  {ex['list']}",
                    example=ex["list"],
                )
            if len(rest) > 1:
                raise ConsoleError(
                    "invalid_argument_count",
                    f"`{cmd} list` 最多接受一个位置参数，收到 {len(rest)} 个。\n  {ex['list']}",
                    example=ex["list"],
                )
            name_or_target = rest[0] if rest else target_opt
            if not name_or_target:
                return _forward(
                    "list_mcp_servers",
                    {},
                    post_process=_filter_servers_by_kind(cmd),
                )
            return _forward(
                "list_mcp_servers",
                {
                    "name_or_target": name_or_target,
                    "tool": options.get("tool"),
                    "args": _maybe_json_object(options.get("args"), "--args", ex["list"]),
                    "kind": cmd,
                    "extra": _maybe_json_object(options.get("extra"), "--extra", ex["list"]),
                },
            )

        if sub == "call":
            _reject_unknown_options(options, frozenset({"tool", "args", "extra", "target", "output"}), ex["call"])
            target_opt = options.get("target")
            if target_opt is not None and rest:
                raise ConsoleError(
                    "ambiguous_input_source",
                    f"target 同时出现在位置参数（{rest[0]!r}）与 --target 里，无法判断用哪个。\n  {ex['call']}",
                    example=ex["call"],
                )
            target = rest[0] if rest else target_opt
            if not target:
                raise ConsoleError(
                    _ERROR_MISSING_TARGET,
                    f"`{cmd} call` 需要 target（spec / URL / 已登记名称）。\n  {ex['call']}",
                    example=ex["call"],
                )
            return _forward(
                call_tool,
                {
                    "target": target,
                    "action": "call",
                    "tool": options.get("tool"),
                    "args": _maybe_json_object(options.get("args"), "--args", ex["call"]),
                    "extra": _maybe_json_object(options.get("extra"), "--extra", ex["call"]),
                },
            )

        if sub == "config":
            action = rest[0].lower() if rest else ""
            args_rest = rest[1:]
            if action == "list":
                _reject_unknown_options(options, frozenset({"output"}), ex["config_list"])
                return _forward("list_mcp_servers", {}, post_process=_filter_servers_by_kind(cmd))
            if action == "get":
                if not args_rest:
                    raise ConsoleError(_ERROR_MISSING_TARGET, f"`{cmd} config get` 需要名称。")
                entry = await registry.resolve_name(args_rest[0])
                if entry is None:
                    raise ConsoleError(_ERROR_NOT_FOUND, f"没有名为 {args_rest[0]!r} 的配置。")
                entry_kind = str(getattr(entry, "kind", "") or "").lower()
                if entry_kind != cmd:
                    # B7：`openapi config get <mcp 名>` 原本照常返回 mcp 配置，等于
                    # 把"三棵子树各管一种 kind"在 get 上放空。这里明确归属并指路。
                    raise ConsoleError(
                        "spec_kind_mismatch",
                        f"{args_rest[0]!r} 属于 {entry_kind}，不是 {cmd}；请改用 "
                        f"`{entry_kind} config get {args_rest[0]}`。",
                        example=f"{entry_kind} config get {args_rest[0]}",
                    )
                return {"success": True, "server": registry.entry_summary(entry)}
            if action == "add":
                _reject_unknown_options(options, frozenset({"note", "config", "overwrite", "output"}), ex["config_add"])
                if len(args_rest) != 2:
                    raise ConsoleError(
                        _ERROR_MISSING_TARGET if len(args_rest) < 2 else "invalid_argument_count",
                        f"`{cmd} config add` 需要 <name> <target> 两个位置参数。\n  {ex['config_add']}",
                        example=ex["config_add"],
                    )
                return _forward(
                    "save_mcp_server",
                    {
                        "name": args_rest[0],
                        "kind": cmd,
                        "protocol": None,
                        "target": args_rest[1],
                        "note": str(options.get("note") or ""),
                        "config": _maybe_json_object(options.get("config"), "--config", ex["config_add"]),
                        "overwrite": bool(options.get("overwrite")) or None,
                    },
                )
            if action == "remove":
                if len(args_rest) != 1:
                    raise ConsoleError(_ERROR_MISSING_TARGET, f"`{cmd} config remove` 需要一个名称。")
                return _forward("delete_mcp_server", {"name": args_rest[0]})
            if action in ("enable", "disable"):
                if not args_rest:
                    raise ConsoleError(_ERROR_MISSING_TARGET, f"`{cmd} config {action}` 需要名称。")
                return _forward("set_mcp_server_enabled", {"name": args_rest[0], "enable": action == "enable"})
            raise ConsoleError(
                _ERROR_UNKNOWN_SUB,
                f"`{cmd} config` 只支持 list / get / add / remove / enable / disable。",
                example=ex["config_list"],
            )

        if sub in ("enable", "disable"):
            _reject_unknown_options(options, frozenset(), ex["enable"])
            if len(rest) != 1:
                raise ConsoleError(_ERROR_MISSING_TARGET, f"`{cmd} {sub}` 需要一个名称。\n  {ex['enable']}")
            return _forward("set_mcp_server_enabled", {"name": rest[0], "enable": sub == "enable"})

        raise ConsoleError(
            _ERROR_UNKNOWN_SUB,
            f"未知的 {cmd} 子命令：{sub}。可用：list / call / config / enable / disable。",
            example=ex["list"],
        )
    except ConsoleError as exc:
        return cp._error_payload(exc)
    except TogoException as exc:
        return {
            "success": False,
            "error_code": getattr(exc, "error_code", f"{cmd}_error"),
            "message": str(exc),
        }
