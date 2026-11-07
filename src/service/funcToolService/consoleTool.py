"""`console` 工具的路由与 help 文本。

与 `consoleParsing` 的分工：那边只管「命令长什么样、调用者有没有权限」，
这边只管「解析完的命令怎么落到原工具上」。分开是为了让权限校验能被单独
变异测试（#154 CC2）——把这里的路由改坏不该影响权限判定。

PATCH 语义（draft §4.3 / #154 CC5）在 `_patch_*` 里实现：先读已有资源，再用
payload 里**出现的**键覆盖。数组字段一旦出现即全量替换，未出现则保持原值。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from service.roomService import ToolCallContext

from . import consoleCommands as cc
from . import consoleParsing as cp
from .consoleParsing import ConsoleError, ParsedCommand

# ---------------------------------------------------------------------------
# help 文本（#154 CC6 要实测它的体积）
#
# 刻意压到最短：只列 资源/操作/一行用途 + 三类输入的一句话说明。
# 详细参数放到 `help <资源> <操作>`，模型按需再查 —— 这比一次性把所有参数
# 灌进 context 省得多，也是 draft §5 的设计意图。
# ---------------------------------------------------------------------------

HELP_SUMMARY = """dept    ls|get|create|update|rm          部门管理
agent   ls|get|create|update|wake|dm      成员管理
room    ls|get|create|update|rm          房间管理
role    ls|get|create|update|rm          角色模板
time    get [--tz]                       当前时间
reload                                   重载团队（会中断当前行动）
用法：<资源> <操作> [位置参数] [--选项 ...]
create/update 的配置输入：'<json>' 或 --from payload（payload 字段传 JSON object）
详情：help <资源> 或 help <资源> <操作>"""

HELP_DETAIL: dict[str, str] = {
    "dept ls": "dept ls —— 列出部门树（含成员、负责人、子部门）。",
    "dept get": "dept get <id|名称> —— 查看部门详情。",
    "dept create": (
        "dept create '<json>' | --from payload\n"
        "字段：name, responsibility, manager_name, member_names[], parent_name, i18n"
    ),
    "dept update": (
        "dept update <id|名称> '<json>' | --from payload\n"
        "PATCH 语义：只改出现的字段；member_names 出现即全量替换。"
    ),
    "dept rm": (
        "dept rm <id|名称> [--recursive] [--confirm <token>]\n"
        "不带 --confirm 时只返回影响范围与 token，不执行删除。"
    ),
    "agent ls": "agent ls —— 列出所有成员。",
    "agent get": "agent get <名称|id> —— 查看成员详情（含房间与可唤醒状态）。",
    "agent create": (
        "agent create '<json>' | --from payload\n"
        "字段：name, role_template_name, model, driver, allow_tools[], allow_skills[], i18n"
    ),
    "agent update": (
        "agent update <名称|id> '<json>' | --from payload\n"
        "PATCH 语义：只改出现的字段；数组出现即全量替换。"
    ),
    "agent wake": "agent wake <名称> —— 唤醒 FAILED 成员。",
    "agent dm": "agent dm <名称> —— 与成员发起私聊（已有房间则直接返回）。",
    "room ls": "room ls —— 列出所有房间。",
    "room get": "room get <id|名称> —— 查看房间详情（含当前轮次与消息数）。",
    "room create": (
        "room create '<json>' | --from payload\n"
        "字段：name, member_names[], initial_topic, max_rounds"
    ),
    "room update": (
        "room update <id|名称> '<json>' | --from payload\n"
        "PATCH 语义：只改出现的字段。"
    ),
    "room rm": (
        "room rm <id|名称> [--confirm <token>]\n"
        "不带 --confirm 时只返回影响范围与 token，不执行删除。"
    ),
    "role ls": "role ls [--kw <关键词>] —— 列出角色模板（不含 soul）。",
    "role get": "role get <名称> —— 查看角色模板详情。",
    "role create": (
        "role create '<json>' | --from payload\n字段：name, type(USER), soul, i18n"
    ),
    "role update": (
        "role update <名称> '<json>' | --from payload\nPATCH 语义：只改出现的字段。"
    ),
    "role rm": "role rm <名称> [--confirm <token>] —— 删除角色模板。",
    "time get": "time get [--tz <IANA时区>] —— 获取当前时间。",
    "reload": "reload —— 重载团队运行时；**会中断当前行动**，需要 --confirm。",
}


def _help_text(tokens: list[str]) -> dict[str, Any]:
    if len(tokens) <= 1:
        return {"success": True, "help": HELP_SUMMARY}
    resource = tokens[1].lower()
    action = tokens[2].lower() if len(tokens) > 2 else ""
    key = f"{resource} {action}".strip()
    detail = HELP_DETAIL.get(key)
    if detail is not None:
        return {"success": True, "help": detail}
    available = sorted(
        k for k in HELP_DETAIL if k == resource or k.startswith(resource + " ")
    )
    if available:
        return {
            "success": True,
            "help": f"{resource} 的可用操作："
            + " | ".join(k.split(" ")[1] if " " in k else "" for k in available),
        }
    raise ConsoleError(
        "unknown_command",
        f"未知命令：'{key}'。可用资源：{' | '.join(cc.RESOURCE_NAMES)}",
    )


# ---------------------------------------------------------------------------
# PATCH 合并辅助
# ---------------------------------------------------------------------------

def _pick(payload: dict[str, Any], key: str, default: Any = None) -> Any:
    return payload[key] if key in payload else default


def _require(payload: dict[str, Any], key: str, command: str) -> Any:
    if key not in payload:
        raise ConsoleError(
            "missing_field",
            f"{command} 缺少必填字段：{key}",
            example=f"help {command}",
        )
    return payload[key]


async def _patch_dept(
    team_id: int, target: str, payload: dict[str, Any], context: ToolCallContext
) -> dict[str, Any]:
    from dal.db import gtAgentManager, gtDeptManager

    existing = await gtDeptManager.get_dept_by_id(int(target)) if target.isdigit() else None
    if existing is None:
        existing = await gtDeptManager.get_dept_by_name(team_id, target.strip())
    if existing is None or existing.team_id != team_id:
        raise ConsoleError("not_found", f"未找到部门：{target}")

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    id_to_name = {a.id: a.name for a in all_agents}

    # 未出现的字段取现值（PATCH），出现的字段用新值。
    manager_name = _pick(payload, "manager_name")
    if manager_name is None:
        manager_name = id_to_name.get(existing.manager_id, "")
    member_names = _pick(payload, "member_names")
    if member_names is None:
        member_names = [id_to_name.get(aid, "") for aid in (existing.agent_ids or [])]

    parent_name: str | None = None
    if "parent_name" in payload:
        parent_name = payload["parent_name"]
    elif existing.parent_id is not None:
        from dal.db import gtDeptManager as _dm

        parent = await _dm.get_dept_by_id(existing.parent_id)
        parent_name = parent.name if parent is not None else None

    from service.funcToolService import tools as legacy

    return await legacy.save_dept(
        name=_pick(payload, "name", existing.name),
        responsibility=_pick(payload, "responsibility", existing.responsibility),
        manager_name=manager_name,
        member_names=member_names,
        parent_name=parent_name,
        i18n=_pick(payload, "i18n", existing.i18n or None),
        overwrite_existing=True,
        dept_id=existing.id,
        _context=context,
    )


async def _patch_agent(
    team_id: int, target: str, payload: dict[str, Any], context: ToolCallContext
) -> dict[str, Any]:
    from dal.db import gtRoleTemplateManager

    existing = await cp._resolve_agent_by_target(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到成员：{target}")

    template = await gtRoleTemplateManager.get_role_template_by_id(existing.role_template_id)
    role_template_name = _pick(payload, "role_template_name")
    if role_template_name is None:
        role_template_name = template.name if template is not None else ""

    from service.funcToolService import tools as legacy

    return await legacy.save_agent(
        name=_pick(payload, "name", existing.name),
        role_template_name=role_template_name,
        model=_pick(payload, "model", existing.model or None),
        driver=_pick(payload, "driver", existing.driver.value if hasattr(existing.driver, "value") else existing.driver),
        allow_tools=_pick(payload, "allow_tools", existing.allow_tools),
        allow_skills=_pick(payload, "allow_skills", existing.allow_skills),
        i18n=_pick(payload, "i18n", existing.i18n or None),
        overwrite_existing=True,
        agent_id=existing.id,
        _context=context,
    )


async def _patch_room(
    team_id: int, target: str, payload: dict[str, Any], context: ToolCallContext
) -> dict[str, Any]:
    from dal.db import gtAgentManager

    existing = await cp._resolve_room_by_target(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到房间：{target}")

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    id_to_name = {a.id: a.name for a in all_agents}
    member_names = _pick(payload, "member_names")
    if member_names is None:
        member_names = [id_to_name.get(aid, "") for aid in (existing.agent_ids or [])]

    from service.funcToolService import tools as legacy

    return await legacy.save_room(
        name=_pick(payload, "name", existing.name),
        member_names=member_names,
        initial_topic=_pick(payload, "initial_topic", existing.initial_topic or ""),
        max_rounds=_pick(payload, "max_rounds", existing.max_rounds),
        overwrite_existing=True,
        room_id=existing.id,
        _context=context,
    )


async def _patch_role(
    target: str, payload: dict[str, Any], context: ToolCallContext
) -> dict[str, Any]:
    existing = await cp._resolve_role_by_target(target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到角色模板：{target}")

    from service.funcToolService import tools as legacy

    return await legacy.save_role_template(
        name=_pick(payload, "name", existing.name),
        type=_pick(payload, "type", existing.type.name),
        soul=_pick(payload, "soul", existing.soul),
        i18n=_pick(payload, "i18n", existing.i18n or None),
        overwrite_existing=True,
        _context=context,
    )


# ---------------------------------------------------------------------------
# 删除保护（#154 D4 / CC4）
# ---------------------------------------------------------------------------

async def _plan_dept_rm(team_id: int, target: str, recursive: bool) -> list[str]:
    existing = await cp._resolve_dept_id(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到部门：{target}")
    from dal.db import gtDeptManager
    from service import deptService

    impact = [f"部门 id={existing}（{target}）"]
    node = await deptService.get_dept_tree(team_id)
    from service.funcToolService.tools import _collect_descendant_ids, _find_dept_node

    current = _find_dept_node(node, existing)
    descendants = sorted(_collect_descendant_ids(current) - {existing}) if current is not None else []
    if descendants:
        if not recursive:
            raise ConsoleError(
                "recursive_required",
                f"部门 {target} 下还有 {len(descendants)} 个子部门；如需一并删除请加 --recursive。",
            )
        for did in descendants:
            dept = await gtDeptManager.get_dept_by_id(did)
            impact.append(f"子部门 id={did}（{dept.name if dept else '?'}）")
    return impact


async def _plan_room_rm(team_id: int, target: str) -> list[str]:
    existing = await cp._resolve_room_by_target(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到房间：{target}")
    return [f"房间 id={existing.id}（{existing.name}，类型 {existing.type.name}）"]


async def _plan_role_rm(target: str) -> list[str]:
    existing = await cp._resolve_role_by_target(target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到角色模板：{target}")
    return [f"角色模板 {existing.name}（类型 {existing.type.name}）"]


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

async def _route(
    parsed: ParsedCommand,
    payload: dict[str, Any] | None,
    context: ToolCallContext | None,
) -> dict[str, Any]:
    from service.funcToolService import tools as legacy

    key = parsed.key
    target = parsed.target

    # ---- 部门 ----
    if key == "dept ls":
        return await legacy.get_dept_info(_context=context)
    if key == "dept get":
        dept_id = await cp._resolve_dept_id(context.team_id, target or "")
        if dept_id is None:
            raise ConsoleError("not_found", f"未找到部门：{target}")
        return await legacy.get_dept_info(dept_id=dept_id, _context=context)
    if key == "dept create":
        data = cp._input_payload_for(parsed, payload)
        return await legacy.save_dept(
            name=_require(data, "name", key),
            responsibility=_require(data, "responsibility", key),
            manager_name=_require(data, "manager_name", key),
            member_names=_pick(data, "member_names", []),
            parent_name=_require(data, "parent_name", key),
            i18n=_pick(data, "i18n"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
            _context=context,
        )
    if key == "dept update":
        data = cp._input_payload_for(parsed, payload)
        return await _patch_dept(context.team_id, target or "", data, context)
    if key == "dept rm":
        recursive = bool(parsed.options.get("recursive"))
        dept_id = await cp._resolve_dept_id(context.team_id, target or "")
        return await _handle_rm(
            parsed, context, target or "",
            planner=lambda: _plan_dept_rm(context.team_id, target or "", recursive),
            executor=lambda: legacy.delete_dept(
                name=target or "",
                dept_id=dept_id,
                recursive=recursive,
                _context=context,
            ),
        )

    # ---- 成员 ----
    if key == "agent ls":
        return await legacy.get_agent_info(_context=context)
    if key == "agent get":
        # 原工具只认 name；`agent get <id>` 要先按 id 解析出 name（draft §4.2
        # 「target 优先按 id，否则按名称」）。按 id 命中不了就原样透传，让底层
        # 报它自己的「未找到成员」——不在这里另造一套错误文案。
        agent_row = await cp._resolve_agent_by_target(context.team_id, target or "")
        return await legacy.get_agent_info(
            agent_name=agent_row.name if agent_row is not None else target,
            _context=context,
        )
    if key == "agent create":
        data = cp._input_payload_for(parsed, payload)
        return await legacy.save_agent(
            name=_require(data, "name", key),
            role_template_name=_require(data, "role_template_name", key),
            model=_pick(data, "model"),
            driver=_pick(data, "driver", "tsp"),
            allow_tools=_pick(data, "allow_tools"),
            allow_skills=_pick(data, "allow_skills"),
            i18n=_pick(data, "i18n"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
            _context=context,
        )
    if key == "agent update":
        data = cp._input_payload_for(parsed, payload)
        return await _patch_agent(context.team_id, target or "", data, context)
    if key == "agent wake":
        return await legacy.wake_up_agent(agent_name=target or "", _context=context)
    if key == "agent dm":
        return await legacy.start_chat(agent_name=target or "", _context=context)

    # ---- 房间 ----
    if key == "room ls":
        return await legacy.get_room_info(_context=context)
    if key == "room get":
        # 同 `agent get`：原工具只认 name，按 id 定位后转成 name 再调。
        room_row = await cp._resolve_room_by_target(context.team_id, target or "")
        return await legacy.get_room_info(
            room_name=room_row.name if room_row is not None else target,
            _context=context,
        )
    if key == "room create":
        data = cp._input_payload_for(parsed, payload)
        return await legacy.save_room(
            name=_require(data, "name", key),
            member_names=_pick(data, "member_names", []),
            initial_topic=_pick(data, "initial_topic", ""),
            max_rounds=_pick(data, "max_rounds"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
            _context=context,
        )
    if key == "room update":
        data = cp._input_payload_for(parsed, payload)
        return await _patch_room(context.team_id, target or "", data, context)
    if key == "room rm":
        room = await cp._resolve_room_by_target(context.team_id, target or "")
        return await _handle_rm(
            parsed, context, target or "",
            planner=lambda: _plan_room_rm(context.team_id, target or ""),
            executor=lambda: legacy.delete_room(
                name=target or "",
                room_id=room.id if room is not None else None,
                _context=context,
            ),
        )

    # ---- 角色模板 ----
    if key == "role ls":
        kw = parsed.options.get("kw")
        keywords = [kw] if kw else None
        return await legacy.list_role_templates(keywords=keywords, _context=context)
    if key == "role get":
        return await legacy.get_role_template(role_name=target or "", _context=context)
    if key == "role create":
        data = cp._input_payload_for(parsed, payload)
        return await legacy.save_role_template(
            name=_require(data, "name", key),
            type=_pick(data, "type", "USER"),
            soul=_require(data, "soul", key),
            i18n=_pick(data, "i18n"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
            _context=context,
        )
    if key == "role update":
        data = cp._input_payload_for(parsed, payload)
        return await _patch_role(target or "", data, context)
    if key == "role rm":
        return await _handle_rm(
            parsed, context, target or "",
            planner=lambda: _plan_role_rm(target or ""),
            executor=lambda: legacy.delete_role_template(role_name=target or "", _context=context),
        )

    # ---- 其他 ----
    if key == "time get":
        return legacy.get_time(timezone=parsed.options.get("tz"))
    if key == "reload":
        return await _handle_reload(parsed, context)

    raise ConsoleError(
        "unknown_command",
        f"未实现的命令：{key}（命令表里有但路由缺失，属实现缺陷）",
    )


async def _handle_rm(
    parsed: ParsedCommand,
    context: ToolCallContext | None,
    target: str,
    *,
    planner,
    executor,
) -> dict[str, Any]:
    """两步删除：不带 token 只报影响范围，带 token 才真删（#154 D4）。"""
    impact = await planner()
    token = parsed.options.get("confirm")
    if not token:
        new_token = cp._issue_confirm_token(
            command_key=parsed.key,
            target=target,
            agent_id=context.agent_id if context is not None else 0,
            team_id=context.team_id if context is not None else 0,
            impact=impact,
        )
        return {
            "success": False,
            "error_code": "confirm_required",
            "message": "删除操作需要确认，尚未执行。确认后请带 --confirm <token> 重试。",
            "impact": impact,
            "confirm_token": new_token,
            "expires_in_seconds": 300,
        }
    ok, code = cp._consume_confirm_token(
        token,
        command_key=parsed.key,
        target=target,
        agent_id=context.agent_id if context is not None else 0,
        team_id=context.team_id if context is not None else 0,
    )
    if not ok:
        raise ConsoleError(
            code,
            "confirm token 无效或已过期，请重新执行不带 --confirm 的命令获取新 token。",
        )
    result = await executor()
    if isinstance(result, dict):
        result = {**result, "impact": impact}
    return result


async def _handle_reload(parsed: ParsedCommand, context: ToolCallContext | None) -> dict[str, Any]:
    """`reload` 会自中断当前 turn（#154 D6）。

    这里**不做普通返回值包装**：`reload_team` 内部会 `create_task(hot_reload)` 后
    挂起在一个永不完成的 future 上，本协程随 turn 被取消。`self_interrupt` 的
    标记与恢复由 `AgentTurnRunner` 负责（见 `toolRegistry.register(self_interrupt=...)`），
    所以这里只需要把调用原样转发出去。

    `--confirm` 只加一层人确认，不改变自中断语义。
    """
    token = parsed.options.get("confirm")
    if not token:
        new_token = cp._issue_confirm_token(
            command_key=parsed.key,
            target="reload",
            agent_id=context.agent_id if context is not None else 0,
            team_id=context.team_id if context is not None else 0,
            impact=["重载团队运行时：会中断团队内正在执行的任务"],
        )
        return {
            "success": False,
            "error_code": "confirm_required",
            "message": "reload 会中断当前行动，需要确认，尚未执行。",
            "impact": ["重载团队运行时：会中断团队内正在执行的任务"],
            "confirm_token": new_token,
            "expires_in_seconds": 300,
        }
    ok, code = cp._consume_confirm_token(
        token,
        command_key=parsed.key,
        target="reload",
        agent_id=context.agent_id if context is not None else 0,
        team_id=context.team_id if context is not None else 0,
    )
    if not ok:
        raise ConsoleError(code, "confirm token 无效或已过期。")

    from service.funcToolService import tools as legacy

    return await legacy.reload_team(_context=context)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

async def console(command: str, payload: Optional[dict] = None, _context: ToolCallContext = None) -> dict:
    """团队/组织/成员/房间/角色模板的管理控制台，用命令行完成管理操作。

    Args:
        command: 要执行的命令。资源名：dept(部门) / agent(成员) / room(房间) / role(角色模板)。示例：'dept ls'、'agent create --from payload'、'room get 研发部'。输入 'help' 查看全部命令。
        payload: 仅当 command 使用 --from payload 时提供，作为 create/update 的 JSON 配置对象；其他命令不得提供。
    """
    try:
        tokens = cp._tokenize(command)
    except ConsoleError as exc:
        return cp._error_payload(exc)

    # `help` 先走：它不解析选项、不校验权限。
    if tokens and tokens[0].lower() in ("help", "?"):
        try:
            return _help_text(tokens)
        except ConsoleError as exc:
            return cp._error_payload(exc)

    spec, err = cc.resolve_command_spec(tokens)
    if spec is None:
        return {
            "success": False,
            "error_code": "unknown_command",
            "message": err or "未知命令",
            "resources": list(cc.RESOURCE_NAMES),
        }

    try:
        rest = tokens[1:]
        if spec.resource == "reload":
            rest = tokens[1:]
        else:
            rest = tokens[2:]
        positional, options, input_source = cp._parse_options(spec, rest)
        parsed = ParsedCommand(
            spec,
            positional=positional,
            options=options,
            input_source=input_source,
            raw_tokens=tokens,
        )
        _check_arity(parsed)
        # 不接受配置输入的命令，传了 payload 必须报错而不是静默忽略（draft §4.5）。
        # 静默忽略的后果很隐蔽：模型以为配置生效了，实际什么都没改，它会接着
        # 报告「已改好」——比直接报错贵得多。
        if payload is not None and not spec.accepts_input:
            raise ConsoleError(
                "ambiguous_input_source",
                f"`{spec.key}` 不接受配置输入，请去掉 payload 字段。",
                example=spec.key,
            )
        await cp._assert_permitted(parsed, _context)
        return await _route(parsed, payload, _context)
    except ConsoleError as exc:
        return cp._error_payload(exc)
    except Exception as exc:  # 业务异常透传（draft §6）
        from service.funcToolService.tools import _tool_error_result

        return _tool_error_result(exc)


def _check_arity(parsed: ParsedCommand) -> None:
    spec = parsed.spec
    count = len(parsed.positional)
    if spec.accepts_input:
        # create：0~1（内联 JSON）或 0（--from payload）
        # update：1~2（target [+ 内联 JSON]）或 1（target + --from payload）
        lower = 1 if spec.needs_target else 0
        upper = 2 if spec.needs_target else 1
        if count < lower or count > upper:
            raise ConsoleError(
                "invalid_argument_count",
                f"`{spec.key}` 需要 {'<target> ' if spec.needs_target else ''}"
                f"{'[<json>]' if spec.needs_target else '<json>'}，收到 {count} 段参数。",
                example=f"help {spec.key}",
            )
        return
    if count < spec.min_args or count > spec.max_args:
        raise ConsoleError(
            "invalid_argument_count",
            f"`{spec.key}` 需要 {spec.min_args}~{spec.max_args} 个位置参数，收到 {count} 个。",
            example=f"help {spec.key}",
        )


# ---------------------------------------------------------------------------
# 动态自中断判定（#154 D6）
# ---------------------------------------------------------------------------

def console_triggers_self_interrupt(tool_call: Any) -> bool:
    """`console` 本次调用是否会自中断当前 turn。

    `AgentTurnRunner` 用它在 handler 执行**之前**决定要不要先写 `SELF_INTERRUPT`
    标记。**必须有这个判定器**：静态 `self_interrupt=True` 会让每条 console 命令
    都留下中断标记（`dept ls` 明明正常返回，重启后却被当成"已执行过的中断工具"
    自动判成功）；静态 `False` 则让 `reload` 丢掉标记，重启后被重放成第二次重载。

    判定口径与 `_handle_reload` 完全对齐：**命令是 `reload` 且带了仍在有效期内的
    `--confirm <token>`**。不带 token 时 `_handle_reload` 只返回 `confirm_required`，
    并不中断，这里也必须返回 False。

    任何解析异常都返回 False（不中断）——判定器在 turn runner 的调用链上，
    抛异常会把一次普通管理命令变成 turn 崩溃，代价远大于漏判。
    """
    try:
        raw_args = tool_call.function_args
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        command = args.get("command")
        if not isinstance(command, str):
            return False
        tokens = cp._tokenize(command)
        spec, _err = cc.resolve_command_spec(tokens)
        if spec is None or not spec.self_interrupt:
            return False
        _positional, options, _source = cp._parse_options(spec, tokens[1:])
        token = options.get("confirm")
        if not token:
            return False
        return cp._confirm_token_is_live(token, command_key=spec.key)
    except Exception:  # noqa: BLE001 —— 判定器绝不把异常抛进 turn runner
        return False
