"""`console` 工具的路由与 help 文本。

与 `consoleParsing` 的分工：那边只管「命令长什么样」（解析、输入来源），
这边只管「解析完的命令怎么落到原工具上」。分开是为了让转发口径能被单独变异测试
（#154 CC2）——把这里的路由改坏不该影响解析与 token 校验。

**权限不在这里算**（#160）：每条子命令的处理方式是把「目标工具名 + 参数」装进
`ForwardToolCall` 抛出去，由唯一执行入口 `AgentToolRegistry.execute_tool_call`
在第 2 跳做鉴权与执行。所以同一个工具「直接调」和「经 console 调」永远走同一份
权限判定，不会出现「原工具调不了、console 却能调」的错位。

PATCH 语义（draft §4.3 / #154 CC5）在 `_patch_*` 里实现：先读已有资源，再用
payload 里**出现的**键覆盖。数组字段一旦出现即全量替换，未出现则保持原值。
读出来的现值全部装进转发参数——PATCH 的合并发生在这里，原工具收到的仍是完整参数。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, NoReturn, Optional

from constants import EmployStatus
from exception import ForwardToolCall
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
        "dept rm <id|名称> [--recursive]\n"
        "有子部门时必须加 --recursive；返回体带 impact 说明实际删掉了什么。"
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
    "room rm": "room rm <id|名称> —— 删除房间；返回体带 impact。",
    "role ls": "role ls [--kw <关键词>] —— 列出角色模板（不含 soul）。",
    "role get": "role get <名称> —— 查看角色模板详情。",
    "role create": (
        "role create '<json>' | --from payload\n字段：name, type(USER), soul, i18n"
    ),
    "role update": (
        "role update <名称> '<json>' | --from payload\nPATCH 语义：只改出现的字段。"
    ),
    "role rm": "role rm <名称> —— 删除角色模板；返回体带 impact。",
    "time get": "time get [--tz <IANA时区>] —— 获取当前时间。",
    "reload": "reload —— 重载团队运行时；**会中断当前行动**。",
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
    team_id: int, target: str, payload: dict[str, Any], parsed: ParsedCommand
) -> NoReturn:
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

    _forward(parsed,
        name=_pick(payload, "name", existing.name),
        responsibility=_pick(payload, "responsibility", existing.responsibility),
        manager_name=manager_name,
        member_names=member_names,
        parent_name=parent_name,
        i18n=_pick(payload, "i18n", existing.i18n or None),
        overwrite_existing=True,
        dept_id=existing.id,
    )


async def _patch_agent(
    team_id: int, target: str, payload: dict[str, Any], parsed: ParsedCommand
) -> NoReturn:
    from dal.db import gtRoleTemplateManager

    existing = await cp._resolve_agent_by_target(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到成员：{target}")

    template = await gtRoleTemplateManager.get_role_template_by_id(existing.role_template_id)
    role_template_name = _pick(payload, "role_template_name")
    if role_template_name is None:
        role_template_name = template.name if template is not None else ""

    _forward(parsed,
        name=_pick(payload, "name", existing.name),
        role_template_name=role_template_name,
        model=_pick(payload, "model", existing.model or None),
        driver=_pick(payload, "driver", existing.driver.value if hasattr(existing.driver, "value") else existing.driver),
        allow_tools=_pick(payload, "allow_tools", existing.allow_tools),
        allow_skills=_pick(payload, "allow_skills", existing.allow_skills),
        i18n=_pick(payload, "i18n", existing.i18n or None),
        overwrite_existing=True,
        agent_id=existing.id,
    )


async def _patch_room(
    team_id: int, target: str, payload: dict[str, Any], parsed: ParsedCommand
) -> NoReturn:
    from dal.db import gtAgentManager

    existing = await cp._resolve_room_by_target(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到房间：{target}")

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    id_to_name = {a.id: a.name for a in all_agents}
    member_names = _pick(payload, "member_names")
    if member_names is None:
        member_names = [id_to_name.get(aid, "") for aid in (existing.agent_ids or [])]

    _forward(parsed,
        name=_pick(payload, "name", existing.name),
        member_names=member_names,
        initial_topic=_pick(payload, "initial_topic", existing.initial_topic or ""),
        max_rounds=_pick(payload, "max_rounds", existing.max_rounds),
        overwrite_existing=True,
        room_id=existing.id,
    )


async def _patch_role(
    target: str, payload: dict[str, Any], parsed: ParsedCommand
) -> NoReturn:
    existing = await cp._resolve_role_by_target(target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到角色模板：{target}")

    _forward(parsed,
        name=_pick(payload, "name", existing.name),
        type=_pick(payload, "type", existing.type.name),
        soul=_pick(payload, "soul", existing.soul),
        i18n=_pick(payload, "i18n", existing.i18n or None),
        overwrite_existing=True,
    )


# ---------------------------------------------------------------------------
# 删除影响范围（#154 D4 / CC4）
#
# 这里的 `resolve` **只做一次**：一次查询同时产出
#   ① `impact` —— 执行后回显给模型的影响范围描述（`impact` 从来不是校验凭据，
#      旧实现把它存进 confirm token 却从不比对；#165 E 把它的定位写清楚）；
#   ② `forward_args` —— 转发给原工具的定位参数（dept_id / room_id / role_name）。
#
# 顺带保留**防误删**校验：`dept rm` 有子部门时必须显式 `--recursive`。
# 这条与「人确认」无关，是「一次误触删掉一棵子树」的物理阻断，不随 confirm 闸门移除。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RmPlan:
    """一次删除请求的解析结果（影响范围 + 转发参数）。"""

    impact: list[str]
    forward_args: dict[str, Any]


async def _plan_dept_rm(team_id: int, target: str, recursive: bool) -> RmPlan:
    """解析部门删除目标：产出影响范围与转发参数，并阻断「未加 --recursive 删子树」。"""
    existing = await cp._resolve_dept_id(team_id, target)
    if existing is None:
        raise ConsoleError("not_found", f"未找到部门：{target}")

    from service import deptService
    from service.funcToolService.tools import _collect_descendant_ids, _find_dept_node

    impact = [f"部门 id={existing}（{target}）"]
    node = await deptService.get_dept_tree(team_id)
    current = _find_dept_node(node, existing)
    descendants = sorted(_collect_descendant_ids(current) - {existing}) if current is not None else []
    if descendants:
        if not recursive:
            raise ConsoleError(
                "recursive_required",
                f"部门 {target} 下还有 {len(descendants)} 个子部门；如需一并删除请加 --recursive。",
            )
        from dal.db import gtDeptManager

        for did in descendants:
            dept = await gtDeptManager.get_dept_by_id(did)
            impact.append(f"子部门 id={did}（{dept.name if dept else '?'}）")
    return RmPlan(
        impact=impact,
        forward_args={"name": target, "dept_id": existing, "recursive": recursive},
    )


async def _plan_room_rm(team_id: int, target: str) -> RmPlan:
    """解析房间删除目标：产出影响范围与转发参数（一次查询复用两者）。"""
    room = await cp._resolve_room_by_target(team_id, target)
    if room is None:
        raise ConsoleError("not_found", f"未找到房间：{target}")
    return RmPlan(
        impact=[f"房间 id={room.id}（{room.name}，类型 {room.type.name}）"],
        forward_args={"name": target, "room_id": room.id},
    )


async def _plan_role_rm(target: str) -> RmPlan:
    """解析角色模板删除目标：产出影响范围与转发参数。"""
    template = await cp._resolve_role_by_target(target)
    if template is None:
        raise ConsoleError("not_found", f"未找到角色模板：{target}")
    return RmPlan(
        impact=[f"角色模板 {template.name}（类型 {template.type.name}）"],
        forward_args={"role_name": template.name},
    )


# ---------------------------------------------------------------------------
# 路由
#
# 每条分支只做两件事：① 把 console 特有的语义补齐（按 id/名称定位目标、PATCH 合并）；
# ② 把「目标工具名 + 完整参数」抛出去。**不查权限、不执行**。
# 转发目标名一律由 `_forward` 从命令表取，路由里不写字面量（#165 B 的唯一性护栏）。
# ---------------------------------------------------------------------------


def _forward(
    parsed: ParsedCommand,
    /,
    *,
    post_process: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    **args: Any,
) -> NoReturn:
    """把本次调用转交给 `parsed.spec.legacy_tool`（#160 唯一出口）。

    目标名**只从命令表取**，不在路由里重复写一遍字面量：否则「声明的等价工具」与
    「实际转发的工具」会各说各话，而 `test_every_legacy_tool_has_a_category` 这类
    静态护栏就再也咬不住漂移。漏填 `legacy_tool` 的后果也随之明确——转发目标为空，
    第 2 跳在注册表里查不到，按「未知工具」拒绝（fail-closed，而不是静默放行）。

    参数组装保持在调用点（与原 `legacy.X(...)` 的字面形式一一对应），所以路由的
    语义改动完全可从 diff 看出来。`_context` 不放进来：它由 `run_tool_call` 按当前
    上下文注入。
    """
    raise ForwardToolCall(parsed.spec.legacy_tool, args, post_process=post_process)


async def _route(
    parsed: ParsedCommand,
    payload: dict[str, Any] | None,
    context: ToolCallContext | None,
) -> dict[str, Any]:
    key = parsed.key
    target = parsed.target

    # ---- 部门 ----
    if key == "dept ls":
        _forward(parsed)
    if key == "dept get":
        dept_id = await cp._resolve_dept_id(context.team_id, target or "")
        if dept_id is None:
            raise ConsoleError("not_found", f"未找到部门：{target}")
        _forward(parsed, dept_id=dept_id)
    if key == "dept create":
        data = cp._input_payload_for(parsed, payload)
        _forward(parsed,
            name=_require(data, "name", key),
            responsibility=_require(data, "responsibility", key),
            manager_name=_require(data, "manager_name", key),
            member_names=_pick(data, "member_names", []),
            parent_name=_require(data, "parent_name", key),
            i18n=_pick(data, "i18n"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
        )
    if key == "dept update":
        data = cp._input_payload_for(parsed, payload)
        await _patch_dept(context.team_id, target or "", data, parsed)
    if key == "dept rm":
        recursive = bool(parsed.options.get("recursive"))
        await _handle_rm(
            parsed,
            planner=lambda: _plan_dept_rm(context.team_id, target or "", recursive),
        )

    # ---- 成员 ----
    if key == "agent ls":
        _forward(parsed)
    if key == "agent get":
        # 原工具只认 name；`agent get <id>` 要先按 id 解析出 name（draft §4.2
        # 「target 优先按 id，否则按名称；同名冲突时提示改用 id」）。
        #
        # 这里必须多做两件事，否则会**静默返回别的成员**：
        # ① 已离职（OFF_BOARD）的成员不在运行时列表里（`agentService` 只加载
        #    ON_BOARD），原工具会按名字找不到 → 直译成「未找到成员」是误导，
        #    因为库里明明有这个人，只是配置还没 reload 生效；
        # ② 名字不唯一时（离职成员名字可被复用）原工具取运行时列表里第一个，
        #    按 id 查 A 却可能拿到同名的 B —— 这正是本用例集要防的「看起来成功」。
        agent_row = await cp._resolve_agent_by_target(context.team_id, target or "")
        agent_name = agent_row.name if agent_row is not None else target
        if agent_row is not None and agent_row.employ_status != EmployStatus.ON_BOARD:
            # 这条判定只读库（不依赖原工具返回体），所以留在转发**之前**：
            # 让「这个人没加载」的报错发生在任何执行之前。
            raise ConsoleError(
                "member_not_on_board",
                f"成员 {agent_row.name}（id={agent_row.id}）当前不在职"
                f"（{agent_row.employ_status.name}），未加载到运行时，"
                "无法返回房间与状态详情。若刚刚创建/修改，需要 reload 后才生效。",
            )
        _forward(parsed,
            agent_name=agent_name,
            post_process=None if agent_row is None else _guard_agent_get(agent_row),
        )
    if key == "agent create":
        data = cp._input_payload_for(parsed, payload)
        _forward(parsed,
            name=_require(data, "name", key),
            role_template_name=_require(data, "role_template_name", key),
            model=_pick(data, "model"),
            driver=_pick(data, "driver", "tsp"),
            allow_tools=_pick(data, "allow_tools"),
            allow_skills=_pick(data, "allow_skills"),
            i18n=_pick(data, "i18n"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
        )
    if key == "agent update":
        data = cp._input_payload_for(parsed, payload)
        await _patch_agent(context.team_id, target or "", data, parsed)
    if key == "agent wake":
        _forward(parsed, agent_name=target or "")
    if key == "agent dm":
        _forward(parsed, agent_name=target or "")

    # ---- 房间 ----
    if key == "room ls":
        _forward(parsed)
    if key == "room get":
        # 同 `agent get`：原工具只认 name，按 id 定位后转成 name 再调。
        room_row = await cp._resolve_room_by_target(context.team_id, target or "")
        _forward(parsed,
            room_name=room_row.name if room_row is not None else target,
        )
    if key == "room create":
        data = cp._input_payload_for(parsed, payload)
        _forward(parsed,
            name=_require(data, "name", key),
            member_names=_pick(data, "member_names", []),
            initial_topic=_pick(data, "initial_topic", ""),
            max_rounds=_pick(data, "max_rounds"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
        )
    if key == "room update":
        data = cp._input_payload_for(parsed, payload)
        await _patch_room(context.team_id, target or "", data, parsed)
    if key == "room rm":
        await _handle_rm(
            parsed,
            planner=lambda: _plan_room_rm(context.team_id, target or ""),
        )

    # ---- 角色模板 ----
    if key == "role ls":
        kw = parsed.options.get("kw")
        keywords = [kw] if kw else None
        _forward(parsed, keywords=keywords)
    if key == "role get":
        _forward(parsed, role_name=target or "")
    if key == "role create":
        data = cp._input_payload_for(parsed, payload)
        _forward(parsed,
            name=_require(data, "name", key),
            type=_pick(data, "type", "USER"),
            soul=_require(data, "soul", key),
            i18n=_pick(data, "i18n"),
            overwrite_existing=bool(_pick(data, "overwrite_existing", False)),
        )
    if key == "role update":
        data = cp._input_payload_for(parsed, payload)
        await _patch_role(target or "", data, parsed)
    if key == "role rm":
        await _handle_rm(
            parsed,
            planner=lambda: _plan_role_rm(target or ""),
        )

    # ---- 其他 ----
    if key == "time get":
        _forward(parsed, timezone=parsed.options.get("tz"))
    if key == "reload":
        await _handle_reload(parsed)

    raise ConsoleError(
        "unknown_command",
        f"未实现的命令：{key}（命令表里有但路由缺失，属实现缺陷）",
    )


def _guard_agent_get(agent_row) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """`agent get <id>` 的 id 一致性守卫（纵深防御）。

    转发后 console 拿不到返回值，只能在 `execute_tool_call` 第 2 跳返回**之后**
    用 `post_process` 校验：运行时按名字解析出的 id 必须与请求的 id 一致，否则
    说明「按 id 查到了同名的另一个人」，必须报错而不是照原样返回别人的详情。

    这里**只做一致性判定**，不碰权限：鉴权已在 `execute_tool_call` 里完成。
    返回结构化失败体（而不是抛异常）——`post_process` 的契约是「进 dict、出 dict」，
    抛异常会把一次工具调用升级成 turn 级异常。
    """

    def _post(result: dict[str, Any]) -> dict[str, Any]:
        returned_id = (result.get("agent") or {}).get("id")
        if result.get("success") and returned_id is not None and returned_id != agent_row.id:
            return cp._error_payload(
                ConsoleError(
                    "ambiguous_target",
                    f"成员名 {agent_row.name} 在运行时对应 id={returned_id}，"
                    f"与请求的 id={agent_row.id} 不一致；请改用 id 定位。",
                )
            )
        return result

    return _post


async def _handle_rm(parsed: ParsedCommand, planner) -> NoReturn:
    """删除命令的唯一执行路径：解析一次目标，真删，再把影响范围并回结果。

    **没有确认闸门**（#165 D）——但下面这条论证**有前提，用前先读**：

    §6.1 适用前提：`keep_legacy_admin_tools()` 为真（默认）。
        此时 D2 把被收敛的 16 个原工具全暴露给模型，`delete_dept` /
        `delete_room` / `delete_role_template` 都能被直接调用，闸门对「真想删的
        模型」不产生任何阻碍（绕过成本 = 换一个工具名），只对「老实走 console
        的调用方」多加一次往返。删掉后 console 与原工具语义一致，符合 #160
        「直接调与经 console 调是同一份判定」的设计前提。

    §6.2 反前提：`TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS=0`（隐藏模式）下**本函数到不了
        目标工具**。`console` 的每条命令都抛 `ForwardToolCall`，由注册表第 2 跳
        执行，而第 2 跳要查 `registered`；`tspDriver._register_host_tools` 只注册
        `get_tools()` 返回的工具，隐藏模式下 `delete_dept` 等不在其中 ⇒ 第 2 跳
        返回「未知工具: delete_dept」，删除**发生不了**。实测隐藏模式下 23/23 条
        带 `legacy_tool` 的命令**全部失败（0 成功）**；其中**能走到转发层**的一律
        返回 `未知工具: <legacy_tool>`（`dept rm` / `room rm` / `role rm` / `reload`
        在实体存在时实测均断在此处），其余在**参数解析 / 实体查找阶段**即失败——
        那时还没抛 `ForwardToolCall`，断因在上游、与 D2 无关。
        这是 #160（`a1e9a502`）「改抛 ForwardToolCall」引入的既有缺陷，不是本单
        造成的：#160 之前 console 直接 `await legacy.delete_dept(...)`、不查注册表，
        隐藏模式下仍能删。
        ⇒ 所以「隐藏模式下闸门是唯一防线」**当前不成立**：那里没有可用通路，
        谈不上拆防线。但**若将来修好隐藏模式的转发**（让 console 真能干活），
        届时闸门确实不存在了，那是真降级——**修那条通路时必须回来重新评估本决策**。

    `impact` 仍然产出并回显：它描述「这次删掉了什么」（`dept rm` 会列出被一并
    删除的子部门，这是原工具返回体里没有的信息），对模型后续判断有用，但它
    **不是校验凭据**——旧实现把它存进 token 却从不比对（#165 E）。
    """
    plan = await planner()

    def _attach_impact(result: dict[str, Any]) -> dict[str, Any]:
        # 失败体不叠 impact：失败时"删掉了什么"没有意义，叠上去反而让模型以为
        # 部分成功。只给成功结果补这一层（旧实现在这里无条件 `{**result, "impact":...}`，
        # 会把 impact 挂到 `{"success": False}` 上）。
        if isinstance(result, dict) and result.get("success") is not False:
            return {**result, "impact": plan.impact}
        return result

    _forward(parsed, post_process=_attach_impact, **plan.forward_args)


async def _handle_reload(parsed: ParsedCommand) -> NoReturn:
    """`reload` 直接转发 `reload_team`（#165 D 移除确认闸门）。

    **它会自中断当前 turn**（#154 D6）：`reload_team` 内部 `create_task(hot_reload)`
    后挂起在一个永不完成的 future 上，本协程随 turn 被取消。`self_interrupt` 的
    标记与恢复由 `AgentTurnRunner` 负责（`register(self_interrupt_predicate=...)`），
    第 2 跳的注册表命中 `reload_team` 自己的静态 `self_interrupt=True`。

    真转发出去后**不会有返回值**——这是设计如此，不是缺陷。
    """
    _forward(parsed)


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
        parsed = _parse_command_tokens(spec, tokens)
        # 不接受配置输入的命令，传了 payload 必须报错而不是静默忽略（draft §4.5）。
        # 静默忽略的后果很隐蔽：模型以为配置生效了，实际什么都没改，它会接着
        # 报告「已改好」——比直接报错贵得多。
        if payload is not None and not spec.accepts_input:
            raise ConsoleError(
                "ambiguous_input_source",
                f"`{spec.key}` 不接受配置输入，请去掉 payload 字段。",
                example=spec.key,
            )
        return await _route(parsed, payload, _context)
    except ConsoleError as exc:
        return cp._error_payload(exc)
    except ForwardToolCall:
        # 转发信号必须原样上抛（#160）：它是控制流，不是业务失败。
        # 这个 `except` 必须排在下面的 `except Exception` **之前**——后者会把任何
        # 异常转成 `_tool_error_result` 失败返回体，转发信号一旦在这里被吞，
        # 统一入口 `execute_tool_call` 就再也看不到它，整个设计静默失效。
        # （任务单只列了 `run_tool_call` 与 `execute_tool_call` 两处，这里是第三处。）
        raise
    except Exception as exc:  # 业务异常透传（draft §6）
        from service.funcToolService.tools import _tool_error_result

        return _tool_error_result(exc)


def _parse_command_tokens(spec: cc.CommandSpec, tokens: list[str]) -> ParsedCommand:
    """把 token 列表解析成 `ParsedCommand` 并校验参数个数（**唯一**解析入口）。

    为什么要抽出来（#165 实测教训）：`console()` 主入口和
    `console_triggers_self_interrupt` 都要做「切选项 + 校验参数个数」，一开始两处各
    抄一遍 —— 结果谓词只解析了命令名、没解析选项，于是 `reload --confirm xxx`（选项
    非法、真执行会在 `_parse_options` 失败）被判定成「会中断」，留下**假的中断标记**。
    两处口径各写一遍就一定会漂移，抽成一个函数后物理上不可能不一致。
    """
    # `reload` 是顶层命令（没有资源词），其余都是 `<资源> <操作>` 两段式。
    rest = tokens[1:] if spec.resource == "reload" else tokens[2:]
    positional, options, input_source = cp._parse_options(spec, rest)
    parsed = ParsedCommand(
        spec,
        positional=positional,
        options=options,
        input_source=input_source,
        raw_tokens=tokens,
    )
    _check_arity(parsed)
    return parsed


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

    判定口径：#165 删掉 confirm 闸门后，`reload` 不再有"带 token 才真执行"的
    前置条件——**只要这条命令能成功执行，它就一定会中断**。旧口径「reload + 仍在
    有效期内的 confirm token」随之失效（那个 token 已经不存在了）。

    **必须走完整的解析链路，不能只看命令名**：`reload --confirm xxx` 现在会在
    `_parse_options` 撞 `unknown_option` 直接失败、根本到不了转发，如果这里只按
    「命令名是 reload」返回 True，历史条目就会带上一个**假的中断标记**——下次该条目
    因别的原因停在 INIT 时，会被 `_advance_step` 当成「已执行过」自动判成功。
    预警一句：这类“谓词说会中断、实际没执行”的错位比漏判更难查，所以这里复刻
    `console()` 的三步（解析选项 → 校验参数个数），口径与真执行路径逐字对齐。

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
        # 走与 console() **完全相同**的解析链路：选项非法 / 参数个数不对的命令会在
        # 执行前就失败，因此不能中断（否则留下假标记）。
        _parse_command_tokens(spec, tokens)
        return True
    except Exception:  # noqa: BLE001 —— 判定器绝不把异常抛进 turn runner
        return False
