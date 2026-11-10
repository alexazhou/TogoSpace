"""`console` 工具：把 16 个团队管理工具收敛成一条命令行。

设计依据是仓库根 `draft_v_console.md`（只读），落地口径见 #154 的 D1–D6。
本模块负责三件事，顺序也是执行顺序：

1. **解析**：`shlex` 切词（绝不 `eval`、绝不调 shell，D5），认出子命令；
2. **校验输入来源**：内联 JSON 与 `--from payload` 二选一且互斥（D1、CC3）。

3. **路由**在 `consoleTool`：它只负责「命令 → 目标工具 + 参数」。

**权限校验不在这里**（#160 起）：`console` 自己不再重算一遍权限，而是把调用
转发给等价的原工具，由唯一执行入口 `AgentToolRegistry.execute_tool_call` 做鉴权。
这样「原工具调不了、console 却能调」的错位在结构上不可能出现——判定只有一份实现。
`consoleCommands` 里每条命令仍然声明 `legacy_tool`，它是**转发目标**（也是
`CATEGORY_CONFIG` 里那一行对应的工具），不是「权限查表的键」。
"""

from __future__ import annotations

import shlex
from typing import Any, Optional

from . import consoleCommands as cc

# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

class ConsoleError(Exception):
    """console 层的结构化错误。`code` 直接作为返回体的 `error_code`。"""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def _error_payload(exc: ConsoleError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error_code": exc.code,
        "message": exc.message,
    }
    payload.update(exc.extra)
    return payload


def _tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError as exc:  # 未闭合引号等
        raise ConsoleError(
            "invalid_command_syntax",
            f"命令引号/转义不合法：{exc}",
            example="dept ls",
        ) from exc


class ParsedCommand:
    """解析结果。`options` 是 `--key value` 或布尔开关。"""

    def __init__(
        self,
        spec: cc.CommandSpec,
        *,
        positional: list[str],
        options: dict[str, Any],
        input_source: str | None,
        raw_tokens: list[str],
    ) -> None:
        self.spec = spec
        self.key = spec.key
        self.resource = spec.resource
        self.action = spec.action
        self.positional = positional
        self.options = options
        self.input_source = input_source
        self.raw_tokens = raw_tokens

    @property
    def target(self) -> str | None:
        return self.positional[0] if self.positional else None


#: 只带值、不带值的布尔开关。
_BOOLEAN_OPTIONS = frozenset({"recursive"})


def _parse_options(spec: cc.CommandSpec, tokens: list[str]) -> tuple[list[str], dict[str, Any], str | None]:
    """把 token 拆成 (位置参数, 选项, 输入来源)。

    `--from` 是输入来源选择器，单独提取，不进 `options`（它不在 `spec.options` 里，
    否则会被「未知选项」误报）。
    """
    positional: list[str] = []
    options: dict[str, Any] = {}
    input_source: str | None = None

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.startswith("--"):
            name, sep, inline_value = token[2:].partition("=")
            name = name.strip()
            if name == "from":
                value = inline_value or (tokens[idx + 1] if idx + 1 < len(tokens) else "")
                if not value:
                    raise ConsoleError(
                        "missing_argument",
                        "--from 需要一个来源值，正确写法：--from payload",
                        example=f"{spec.key} --from payload",
                    )
                if not sep:
                    idx += 1
                input_source = value.strip()
                idx += 1
                continue
            if name not in spec.options:
                hint = (
                    f"可用选项：{' | '.join('--' + o for o in spec.options)}"
                    if spec.options else "这条命令不接受选项"
                )
                raise ConsoleError(
                    "unknown_option",
                    f"未知选项：--{name}。{hint}",
                    example=spec.key,
                )
            if name in _BOOLEAN_OPTIONS:
                options[name] = True
                idx += 1
                continue
            if sep:
                options[name] = inline_value
                idx += 1
                continue
            if idx + 1 >= len(tokens):
                raise ConsoleError(
                    "missing_argument",
                    f"--{name} 需要一个值",
                    example=f"{spec.key} ... --{name} <值>",
                )
            options[name] = tokens[idx + 1]
            idx += 2
            continue
        positional.append(token)
        idx += 1

    return positional, options, input_source


# ---------------------------------------------------------------------------
# 配置输入（#154 D1：v1 只有内联 JSON + --from payload）
# ---------------------------------------------------------------------------

def _resolve_input_payload(
    parsed: ParsedCommand, payload: dict[str, Any] | None
) -> dict[str, Any]:
    """按 D1 与 CC3 解析配置输入，返回最终的 JSON object。

    四种情形**必须严格区分**（含糊报「解析失败」等于让模型没法自查）：
    - `--from payload` + 无 payload → `missing_payload`
    - `--from payload` + 内联 JSON 同时出现 → `ambiguous_input_source`
    - 未用 `--from` 却传了 payload → `ambiguous_input_source`
    - `--from local:...` / heredoc → `unsupported_input_source`（v1 明确不支持）
    """
    source = parsed.input_source
    positional = parsed.positional

    if source is not None:
        normalized = source.strip()
        if normalized.startswith("local:"):
            raise ConsoleError(
                "unsupported_input_source",
                "v1 暂不支持 local:<path> 文件来源；请改用内联 JSON 或 --from payload。",
                supported=list(cc.SUPPORTED_FROM_SOURCES),
            )
        if normalized.startswith("<<"):
            raise ConsoleError(
                "unsupported_input_source",
                "v1 暂不支持 heredoc 多行输入；请改用内联 JSON 或 --from payload。",
                supported=list(cc.SUPPORTED_FROM_SOURCES),
            )
        if normalized != "payload":
            raise ConsoleError(
                "unsupported_input_source",
                f"未知的输入来源：{normalized!r}；v1 只支持 payload。",
                supported=list(cc.SUPPORTED_FROM_SOURCES),
            )
        # --from payload
        if positional:
            raise ConsoleError(
                "ambiguous_input_source",
                "--from payload 与内联 JSON 不能同时使用。",
                example=f"{parsed.key} --from payload   （配置放 payload 字段）",
            )
        if payload is None:
            raise ConsoleError(
                "missing_payload",
                "使用 --from payload 时必须在 payload 字段提供配置对象。",
                example=f"{parsed.key} --from payload",
            )
        return _require_json_object(payload)

    # 未使用 --from
    if payload is not None:
        raise ConsoleError(
            "ambiguous_input_source",
            "提供了 payload 但命令没有用 --from payload；请二选一。",
            example=f"{parsed.key} --from payload   或   {parsed.key} '{{...}}'",
        )

    if not positional:
        # `input_optional`（#187）：少数命令的配置输入是可选的（目前只有 `task update`
        # ——「撤销验收人」这种调用本来就没有配置对象）。此时「无输入」返回空对象，
        # 由路由层判断「输入 + 选项全都为空」并报 `no_update_action`。
        # **默认 False**，所以 dept/agent/room/role 的 create/update 语义零变化。
        #
        # 为什么不在这里顺手检查「选项也空」：那让解析层知道某条命令的选项语义，
        # 而「没输入且没动作算不算合法」是**命令的业务语义**，归路由。解析层只回答
        # 「这条命令允不允许没有输入」——这一条写在命令表里（`CommandSpec.input_optional`）。
        if parsed.spec.input_optional:
            return {}
        raise ConsoleError(
            "missing_input_source",
            "create/update 需要配置输入：内联 JSON 或 --from payload。",
            example=f"{parsed.key} '{{\"name\":\"示例\"}}'",
        )
    if len(positional) > 1:
        raise ConsoleError(
            "ambiguous_input_source",
            f"只能提供一个配置输入，收到 {len(positional)} 段：{positional!r}",
        )
    raw = positional[0]
    if raw.lstrip().startswith("<<"):
        raise ConsoleError(
            "unsupported_input_source",
            "v1 暂不支持 heredoc 多行输入；请改用内联 JSON 或 --from payload。",
            supported=list(cc.SUPPORTED_FROM_SOURCES),
        )
    return _parse_inline_json(raw)


def _parse_inline_json(raw: str) -> dict[str, Any]:
    import json as _json

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise ConsoleError(
            "invalid_source_json",
            f"内联 JSON 解析失败：{exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）",
            example="dept create '{\"name\":\"x\",\"responsibility\":\"y\"}'",
        ) from exc
    return _require_json_object(data)


def _require_json_object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConsoleError(
            "invalid_payload_field",
            f"配置必须是 JSON object，收到 {type(data).__name__}。",
        )
    return data


def _input_payload_for(parsed: ParsedCommand, payload: dict[str, Any] | None) -> dict[str, Any]:
    """create/update 用：位置参数里「配置」占第几位。

    - `create <json>`：第 0 位是配置。
    - `update <target> <json>`：第 0 位是 target，第 1 位是配置。
    """
    if not parsed.spec.accepts_input:
        return {}
    if parsed.spec.needs_target:
        config_tokens = parsed.positional[1:]
        restored = ParsedCommand(
            parsed.spec,
            positional=config_tokens,
            options=parsed.options,
            input_source=parsed.input_source,
            raw_tokens=parsed.raw_tokens,
        )
        return _resolve_input_payload(restored, payload)
    return _resolve_input_payload(parsed, payload)


# ---------------------------------------------------------------------------
# 目标定位辅助
# ---------------------------------------------------------------------------

async def _resolve_dept_id(team_id: int, target: str) -> int | None:
    from dal.db import gtDeptManager

    stripped = target.strip()
    if stripped.isdigit():
        dept = await gtDeptManager.get_dept_by_id(int(stripped))
        if dept is not None and dept.team_id == team_id:
            return dept.id
        return None
    dept = await gtDeptManager.get_dept_by_name(team_id, stripped)
    return dept.id if dept is not None else None


async def _resolve_room_by_target(team_id: int, target: str):
    from dal.db import gtRoomManager

    stripped = target.strip()
    if stripped.isdigit():
        room = await gtRoomManager.get_room_by_id(int(stripped))
        if room is not None and room.team_id == team_id:
            return room
        return None
    return await gtRoomManager.get_room_by_team_and_name(team_id, stripped)


async def _resolve_agent_by_target(team_id: int, target: str):
    """按 id 或名称定位成员；**同名多行时必须报错要求改用 id**（draft §4.2）。

    只有成员表允许同名：`GtAgent` 的 `(team_id, name)` 是**非唯一**索引（离职成员的
    名字可以被新成员复用，见 `gtAgent.model` 注释）。部门/房间是唯一索引，角色模板
    `name` 本身 unique，所以「同名」这件事只发生在成员上。

    为什么不能像原工具那样「取第一条」：`gtAgentManager.get_agent()` 用
    `aio_get_or_none` → `peewee_async` 的 `aio_get` 是 `paginate(1, 1)`，**不校验唯一性**，
    两行同名时它返回行序靠前的那一行（通常是先建的、已离职的那一个）。`update` 走这条
    路会**静默改到离职成员**，`get` 会静默给出别人的详情 —— 都不报错，模型会当成成功。
    draft §4.2 因此要求「同名冲突时提示改用 id」，这里按设计稿执行。
    """
    from dal.db import gtAgentManager

    stripped = target.strip()
    if stripped.isdigit():
        agent = await gtAgentManager.get_agent_by_id(int(stripped))
        if agent is not None and agent.team_id == team_id:
            return agent
        return None
    # **非 id 路径必须用 status=None 取全量再自己筛**：`get_agent` 默认只查 ON_BOARD，
    # 而新建成员在 `reload_team` 之前是 OFF_BOARD（`save_agent` 建人时就是 OFF_BOARD），
    # 用默认值会导致「刚 create 出来的成员，同一次 turn 里 update 立刻 not_found」。
    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    matched = [a for a in all_agents if a.name == stripped]
    if not matched:
        return None
    if len(matched) > 1:
        raise ConsoleError(
            "ambiguous_target",
            f"团队内有 {len(matched)} 个同名成员 {stripped}（含离职成员）；请改用 id 定位。",
            candidates=[
                {"id": a.id, "name": a.name, "employ_status": a.employ_status.name}
                for a in matched
            ],
        )
    return matched[0]


async def _resolve_role_by_target(target: str):
    from dal.db import gtRoleTemplateManager

    return await gtRoleTemplateManager.get_role_template_by_name(target.strip())
