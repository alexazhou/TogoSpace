"""`console` 工具：把 16 个团队管理工具收敛成一条命令行。

设计依据是仓库根 `draft_v_console.md`（只读），落地口径见 #154 的 D1–D6。
本模块负责四件事，顺序也是执行顺序：

1. **解析**：`shlex` 切词（绝不 `eval`、绝不调 shell，D5），认出子命令；
2. **校验输入来源**：内联 JSON 与 `--from payload` 二选一且互斥（D1、CC3）；
3. **权限**：按子命令等价的原工具 category 逐个查调用者（D3，本单唯一不可降级项）；
4. **路由**：调原工具函数，并对 `update` 做 PATCH 合并、对 `rm` 做 confirm_token 保护（D4）。

为什么权限校验不放在命令表里一次性决定：`console` 本身注册为 BASIC（否则所有人都
失去查询能力），所以「有没有权限调这条子命令」只能**在运行时按子命令**判断。
`consoleCommands.COMMAND_SPECS` 里每条命令都填了 `legacy_tool`，这里就按它去查
`CATEGORY_CONFIG`；漏填 `legacy_tool` 的命令会在校验时被判为无权限（fail-closed）。
"""

from __future__ import annotations

import shlex
import time
import uuid
from typing import Any, Optional

from constants import ToolCategory
from service.roomService import ToolCallContext

from . import consoleCommands as cc

# ---------------------------------------------------------------------------
# confirm_token 存储（#154 D4）：进程内存 + 有效期，**不落库**。
# 理由：这是「人确认一次」的短期凭据，落库会引入清理与跨进程一致性问题，
# 而它本身不承载任何需要持久化的语义（重启发 token 即可）。
# ---------------------------------------------------------------------------
_CONFIRM_TTL_SECONDS = 300

#: token -> {command_key, target, agent_id, team_id, expires_at, impact}
_confirm_tokens: dict[str, dict[str, Any]] = {}


def _purge_expired_tokens(now: float | None = None) -> None:
    current = time.time() if now is None else now
    expired = [key for key, meta in _confirm_tokens.items() if meta["expires_at"] <= current]
    for key in expired:
        _confirm_tokens.pop(key, None)


def _issue_confirm_token(
    *, command_key: str, target: str, agent_id: int, team_id: int, impact: list[str]
) -> str:
    _purge_expired_tokens()
    token = uuid.uuid4().hex[:12]
    _confirm_tokens[token] = {
        "command_key": command_key,
        "target": target,
        "agent_id": agent_id,
        "team_id": team_id,
        "expires_at": time.time() + _CONFIRM_TTL_SECONDS,
        "impact": impact,
    }
    return token


def _consume_confirm_token(
    token: str, *, command_key: str, target: str, agent_id: int, team_id: int
) -> tuple[bool, str]:
    """校验并**一次性消耗** token。返回 (ok, 错误码)。"""
    _purge_expired_tokens()
    meta = _confirm_tokens.get(token)
    if meta is None:
        return False, "invalid_confirm_token"
    if meta["expires_at"] <= time.time():
        _confirm_tokens.pop(token, None)
        return False, "expired_confirm_token"
    bound = (meta["command_key"], meta["target"], meta["agent_id"], meta["team_id"])
    if bound != (command_key, target, agent_id, team_id):
        # 同一个 token 不能挪用到别的命令/目标/调用者上（否则等于免确认）。
        return False, "invalid_confirm_token"
    _confirm_tokens.pop(token, None)
    return True, ""


def _reset_confirm_tokens() -> None:
    """测试用：清空 token 表。"""
    _confirm_tokens.clear()


def _confirm_token_is_live(token: str, *, command_key: str) -> bool:
    """**只读**探测 token 是否仍可用于该命令（不消耗）。

    存在的理由只有一个：自中断判定发生在 handler 执行**之前**（`AgentTurnRunner`
    要看 `self_interrupt` 才知道要不要先打中断标记），而 `console reload` 只有在
    带**有效** confirm token 时才会真去调 `reload_team`。如果这一步返回 True 但
    实际没中断，历史条目上会留下一个误导性的 `SELF_INTERRUPT` 标记——下次该条目
    处于 `INIT`（被别的原因此取消）时会被当成「已执行过」自动判成功。

    这里只校验「命令 + 未过期」，不校验 agent/team 绑定：绑定的最终裁决仍在
    `_consume_confirm_token`，重复一次只会让两处口径有机会漂移。
    """
    _purge_expired_tokens()
    meta = _confirm_tokens.get(token)
    if meta is None:
        return False
    if meta["expires_at"] <= time.time():
        return False
    return meta["command_key"] == command_key


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

_CONFIRM_HANDLED_SEPARATELY = frozenset({"confirm"})


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
# 权限（#154 D3）
# ---------------------------------------------------------------------------

async def _resolve_allowed_categories(team_id: int, agent_id: int) -> tuple[set[ToolCategory], set[str]]:
    """算出调用者运行时实际拥有的 (类别集合, 显式工具名集合)。

    口径必须与 `toolRegistry.build_runtime_allow_specs` 一致，否则会出现
    「原工具调不了、console 却能调」或反过来的错位：
    - `allow_tools is None` → Basic/Read/Write/Execute（**不含 Admin**）
    - 始终补 `Category:Basic`
    - 调用者是团队根主管 → 补 `Category:Admin`
    """
    from dal.db import gtAgentManager
    from service import deptService
    from service.agentService import toolRegistry

    gt_agent = await gtAgentManager.get_agent_by_id(agent_id)
    if gt_agent is None or gt_agent.team_id != team_id:
        return set(), set()

    is_root_leader = False
    dept_root = await deptService.get_dept_tree(team_id)
    if dept_root is not None and dept_root.manager_id == agent_id:
        is_root_leader = True

    specs = toolRegistry.build_runtime_allow_specs(
        gt_agent.allow_tools, is_root_leader=is_root_leader
    )

    categories: set[ToolCategory] = set()
    explicit_names: set[str] = set()
    for spec in specs:
        category = ToolCategory.from_spec(spec)
        if category is not None:
            categories.add(category)
            continue
        explicit_names.add(spec)
    return categories, explicit_names


async def _assert_permitted(parsed: ParsedCommand, context: ToolCallContext | None) -> None:
    """按子命令等价的原工具 category 校验权限。

    `help` 是纯本地命令，不碰数据，跳过校验（否则没有 Admin 的人连 help 都看不到，
    反而更容易瞎试命令）。
    """
    if parsed.spec.key == "help":
        return
    if context is None or not context.agent_id or context.team_id <= 0:
        raise ConsoleError(
            "permission_denied",
            "无法确定调用者身份，拒绝执行。",
        )

    from service.agentService import toolRegistry

    legacy_tool = parsed.spec.legacy_tool
    if not legacy_tool:
        # fail-closed：命令表漏填等价工具名 ⇒ 一律拒绝，绝不「没填就放行」。
        raise ConsoleError(
            "permission_denied",
            f"内部错误：命令 {parsed.key} 未声明等价工具，已按无权限处理。",
        )

    categories, explicit_names = await _resolve_allowed_categories(
        context.team_id, context.agent_id
    )
    legacy_category = toolRegistry.CATEGORY_CONFIG.get(legacy_tool)
    allowed = legacy_tool in explicit_names or (
        legacy_category is not None and legacy_category in categories
    )
    if not allowed:
        raise ConsoleError(
            "permission_denied",
            f"无权限执行 `{parsed.key}`：它等价于 {legacy_tool}"
            f"（类别 {legacy_category.name if legacy_category else '未知'}），当前调用者不具备该权限。",
            legacy_tool=legacy_tool,
            required_category=legacy_category.name if legacy_category else None,
        )


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
    from dal.db import gtAgentManager

    stripped = target.strip()
    if stripped.isdigit():
        agent = await gtAgentManager.get_agent_by_id(int(stripped))
        if agent is not None and agent.team_id == team_id:
            return agent
        return None
    # **必须传 status=None**：`get_agent` 默认只查 ON_BOARD，而新建成员在
    # `reload_team` 之前是 OFF_BOARD（`save_agent` 建人时就是 OFF_BOARD）。
    # 用默认值会导致「刚 create 出来的成员，同一次 turn 里 update 立刻 not_found」。
    # 这与 `save_agent` 自己定位已有成员的口径一致（它也是 `status=None`）。
    return await gtAgentManager.get_agent(team_id, stripped, status=None)


async def _resolve_role_by_target(target: str):
    from dal.db import gtRoleTemplateManager

    return await gtRoleTemplateManager.get_role_template_by_name(target.strip())
