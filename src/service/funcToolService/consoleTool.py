"""`console` 工具的命令行入口：只做收集与拼接，不认识任何具体工具名。

所以这里不能按工具名写死分支，也不能留一张手抄的命令表——新增一条命令就得改两处，help 与解析各走一套。命令由 `@tool(console=ConsoleCommand(...))` 声明、`consoleRegistry.get_table()` 派生，help 的用法与参数也全部派生，只补 `ToolSpec.help` / `ConsoleCommand.help`。

要手写的只有派生不出来的三类：全局用法说明、`ConsoleError` 的 code/message/example、类型转换（正整数、CSV、JSON 字面量、布尔）。设计见 `docs/design/2026-11-20-console-tool-metadata/console_tool_metadata_design.md` §5.1 / §5.3。
"""

from __future__ import annotations

import json
from typing import Any, NoReturn, Optional, Sequence

from constants import ALLOWED_CLEAR_FIELDS, ToolVisibility
from exception import ForwardToolCall
from service.roomService import ToolCallContext
from toolResult import error_result

from . import consoleCommands as cc
from . import consoleParsing as cp
from . import consoleMcp as mcp_console
from . import consoleRegistry as registry
from . import consoleSchema as cs
from . import consoleSpec as spec_console
from .consoleParsing import ConsoleError
from .toolSpec import tool

__all__ = ["console", "console_triggers_self_interrupt", "HELP_SUMMARY", "HELP_DETAIL"]


# ---------------------------------------------------------------------------
# help 文本（#154 CC6 要实测它的体积）
#
# 刻意压到最短：只列 资源/操作 + 三类输入的一句话说明。详细参数放到
# `help <资源> <操作>`，模型按需再查 —— 这比一次性把所有参数灌进 context 省得多，
# 也是 draft §5 的设计意图。摘要与详情**都由命令表派生**（P0 ③），手抄副本已删。
# ---------------------------------------------------------------------------


def _build_help_summary(specs: Optional[tuple[registry.ResolvedCommand, ...]] = None) -> str:
    """一行一个资源，操作按命令表顺序拼接（摘要里**不出现**参数清单）。

    `specs` 为空时用整张命令表（供结构测试使用）；`help` 输出则在运行时传入可见
    子集，避免把 D2 开关状态烘焙进 import 期常量。
    """
    table = registry.get_table()
    commands = table.specs if specs is None else specs
    grouped: dict[str, list[registry.ResolvedCommand]] = {}
    for cmd in commands:
        grouped.setdefault(cmd.resource, []).append(cmd)
    width = max((len(resource) for resource in grouped), default=0) + 2
    lines: list[str] = []
    for resource, resource_commands in grouped.items():
        actions = "|".join(cmd.primary_action for cmd in resource_commands if cmd.primary_action)
        lines.append(f"{resource:<{width}}{actions}".rstrip())
    # mcp / openapi / graphql 子树由各自的窄解析器提供（P3 才做 extension 化，
    # 设计 §6 P0 不做边界：本期照旧由 `console()` 硬分派），摘要里给两行路标。
    lines.append(
        "mcp：MCP 服务用 mcp list / mcp call <server-name>.<tool-name> --args '{...}' / "
        "mcp session start|status|stop"
    )
    lines.append(
        "openapi / graphql：分别用 openapi list|call <spec-or-name> --tool <name> / "
        "graphql list|call <url-or-name> --tool <name>"
    )
    lines.append("用法：<资源> <操作> [位置参数] [--选项 ...]")
    # need_fix_4 §三.5：定位规则原先散在各命令的 overlay 里，模型得先 help 到具体操作才
    # 知道该传 id 还是名称，而传错定位形态是 invalid_argument_count 的第一大来源。
    lines.append(
        "定位规则：get/update 用正整数 <id>；agent dm|wake 与 role 用 <名称>；"
        "dept|room rm 支持 <id> 或 --name 二选一"
    )
    lines.append("create/update 的配置输入（可选）：'<json>' 或 --from payload（payload 字段传 JSON object），也可只用 --选项")
    lines.append(
        "选项重复：数组型（如 --status / --member-names）可重复出现，按出现顺序聚合并去重保序；"
        "数组型的值可用 CSV（a,b）或 JSON 数组（[\"a\",\"b\"]），两种写法等价；"
        "标量型重复出现报 duplicate_option（不会静默只认最后一个）；布尔开关重复无副作用"
    )
    lines.append("详情：help <资源> 或 help <资源> <操作>；任意命令加 --help / -h 亦可")
    return "\n".join(lines)


#: `--remove-field` 可清除字段清单的**占位符**（#187 裁定③a）。
#:
#: 不让 help 文案手抄一份字段名：`taskService` 里曾残留一份手写副本遮蔽了派生常量，
#: 扩 `ClearField` 对运行时毫无影响，而变异自证照样「红」——红错了理由（#181 现场）。
_CLEAR_FIELDS_PLACEHOLDER = "{ALLOWED_CLEAR_FIELDS}"


#: 这张表只放「命令级事实」：参数清单与用法行里推不出来的那些。
#:
#: 算不算重复，以渲染出来的 help 文本为准，不看两句话意思是否接近：把已经渲染出来的内容再写一遍，help 里同一件事就出现两次；反过来删表里已有的条目，`test_console_meta` / `test_console_usability_pack` 会变红。全局规则（重复选项、CSV / JSON 等价）的正本在 `_build_help_summary`，这里不重复。
#: 加条目只写四类：错误码、服务层拒绝条件、PATCH 之外的副作用、跨命令指引（「改已有 X 请用 Y」）。为什么不搬进 `@tool(help=...)` 见设计 §5.3。
_HELP_OVERLAY: dict[str, tuple[str, ...]] = {
    # 服务层拒绝条件 + 跨命令指引（后者见口径 3）。
    "dept create": (
        "同名部门已存在会被直接拒绝。",
        "改已有部门请用 `dept update <id>`（定位只接受正整数 ID）。",
    ),
    "agent create": (
        "同名成员已存在会被直接拒绝。",
        "改已有成员请用 `agent update <id>`（同名成员不再产生歧义）。",
    ),
    "room create": (
        "同名房间已存在会被直接拒绝。",
        "改已有房间请用 `room update <id>`。",
    ),
    # 定位约束与「不许静默成功」都是命令级语义（口径 2）；`member_names` 的全量替换语义
    # 在 dept update 的参数描述里**没有**（描述只有「部门成员名称列表」），故保留。
    "dept update": (
        "member_names 出现即全量替换。",
        "定位只接受正整数 <id>（不接受按名称定位），--name 表示改成的新名称。",
        "没有任何字段可改时返回 no_update_action（不许静默成功）。",
    ),
    "dept rm": (
        "有子部门且未加 --recursive 时由服务层以 DEPT_HAS_CHILDREN 拒绝，不会部分删除。",
        "加 --dry-run 只预演（跑完全部校验，不写库），返回体里是 would_delete。",
    ),
    "agent update": (
        "数组出现即全量替换。",
        "定位只接受 agent_id（同名成员不再歧义），--name 表示改成的新名称。",
        "没有任何字段可改时返回 no_update_action（不许静默成功）。",
    ),
    # dept get / room get：与 agent get 同一句矛盾（参数描述说的是**工具签名**可以
    # id 或 name 二选一，命令行只有一个位置槽）。need_fix_4 §三.4 只点名 agent get，
    # 这里把同一句一并统一，避免三条 get 的定位口径各说一套。
    "dept get": (
        "定位只有位置参数 <id>：按名称请先用 `dept ls` 取 ID；工具层的 name 参数只能用 --from payload 传。",
    ),
    "room get": (
        "定位只有位置参数 <id>：按名称请先用 `room ls` 取 ID；工具层的 name 参数只能用 --from payload 传。",
    ),
    "agent get": (
        # need_fix_4 §三.4：旧文案写「按名字查询走运行时列表」，与参数行「不接受按名称
        # 定位」直接打架——照着这行去按名字查必然被 invalid_target 拒。这里只说 id
        # 查询会碰到的运行时候选，不再暗示有「按名字」这条路。
        "状态与房间取自运行时：离职成员报 member_not_on_board，在职未加载报 agent_runtime_not_loaded。",
        # 参数描述里的「与 name 二选一」讲的是**工具签名**；命令行只有一个位置槽，
        # 不传 id 就没有 name 这条路。不写清楚，位置参数行读起来仍然自相矛盾。
        "定位只有位置参数 <id>：按名称定位请先 `agent ls` 取 ID；工具层的 name 参数只能用 --from payload 传。",
    ),
    "agent dm": ("已有房间则直接返回。",),
    "room update": (
        "定位只接受 room_id，--name 表示改成的新名称。",
        "没有任何字段可改时返回 no_update_action（不许静默成功）。",
    ),
    "room rm": ("DEPT 房间与运行中的房间不允许删除。",),
    "role update": ("目标模板必须已存在。",),
    "role create": ("只能创建 USER 类型；SYSTEM 内置模板只读。",),
    "role rm": ("只能删除 USER 类型且当前未被任何成员使用的模板。",),
    "task ls": (
        "只列未完结任务（要含已完结请用 --status 显式列出，如 --status DONE,CANCELLED）。",
        "状态：TODO / PENDING / IN_PROGRESS / ON_HOLD / REVIEWING / DONE / CANCELLED",
        "过滤项 --assignee / --reviewer / --scope / --kw 全缺时由 list_tasks 自身补 --scope self"
        "（只查我的待办）并在返回体 auto_filled_filters 里说明；要查全团队请显式给 --scope team。",
    ),
    "task create": (
        "建单不会自动唤醒执行人：建完请另发 send_chat_msg 通知对方（返回体 hint 字段会再提醒一次）。",
        "必填 --title / --assignee-id；其余可选。数组/对象型参数（--depends-on）"
        "建议用 --from payload 传，避免引号被 shell 拆坏。",
    ),
    "task update": (
        "撤销验收人用 --remove-field reviewer_id。",
        "可清除的字段：" + _CLEAR_FIELDS_PLACEHOLDER + "（白名单外的字段会被拒绝并列出候选）。",
        "⚠️ 数组型选项可重复出现（按出现顺序聚合、去重保序）："
        "--remove-field a --remove-field b 等价于 --remove-field a,b；"
        "标量选项重复出现会报 duplicate_option，布尔开关重复无副作用。",
        "不传 note 不会清空已有 note（参数默认空串，不会覆盖）。",
        "既没有配置输入也没有 --remove-field 时返回 no_update_action（不许静默成功）。",
    ),
    # 「会中断当前行动」是必然性表述（`ToolSpec.self_interrupt`）；工具 docstring 写的是
    # 「可能中断团队内正在执行的任务」，两处措辞不一致属既有事实，本单只报不改（改
    # docstring 会进模型 schema，那是行为面）。
    "reload": ("重载团队运行时；**会中断当前行动**。",),
}


def _render_help(text: str) -> str:
    """占位符在**返回前**替换成当刻的 `ALLOWED_CLEAR_FIELDS`（#187 裁定③a）。

    候选字段集合因此永远跟着常量走，扩枚举后 help 自动更新，不会退化成快照。
    模块级 `ALLOWED_CLEAR_FIELDS` 这个名字必须保留：`test_console_task` 用
    monkeypatch 替换它来验证「候选集合真的源自该常量」。
    """
    return text.replace(_CLEAR_FIELDS_PLACEHOLDER, " / ".join(sorted(ALLOWED_CLEAR_FIELDS)))


def _tool_description(cmd: registry.ResolvedCommand) -> str:
    """命令的「一行用途」= 工具 docstring 首段首句（派生，不手抄）。"""
    if cmd.tool is None:
        return ""
    from .funcToolType import get_function_metadata

    try:
        metadata = get_function_metadata(cmd.tool.name, cmd.tool.callable)
    except Exception:  # noqa: BLE001 —— 派生失败只降级
        return ""
    return cs._first_clause(metadata.get("description") if isinstance(metadata, dict) else "")


def _positional_tokens(cmd: registry.ResolvedCommand, arg: Any) -> tuple[str, str]:
    """位置槽在签名里的呈现 + 裸 token：`("<id>" | "[<id>]", "<id>")`。

    **签名与「位置参数：」段共用的唯一来源**（#323 方案 B 要求两处逐字一致）。
    裸 token 不带可选括号、不带 `--`：可选性由参数行的「（必填）/（可选）」表达。

    占位规则（与历史签名逐字节一致）：
    - `int` 位置槽 → `<id>`，可选时签名里加方括号 → `[<id>]`；
    - 单 `str` 位置槽 → `<名称>`，可选时 `[<名称>]`；
    - 多 `str` 位置槽按 `Target.name` 逐个渲染 → `<container_id>` / `<note>`。
    """
    kind = cmd.param_kinds.get(arg.target.name, "string")
    effective = arg.target.type or (int if kind == "integer" else str)
    if effective is int:
        return ("<id>" if arg.required else "[<id>]"), "<id>"
    if len(cmd.positional_args) == 1:
        return ("<名称>" if arg.required else "[<名称>]"), "<名称>"
    token = f"<{arg.target.name}>"
    return token, token


def _usage_line(cmd: registry.ResolvedCommand) -> str:
    """由**命令声明**拼出用法行：位置槽按 `Arg` 的顺序与必填性渲染（§5.3）。"""
    parts = [cmd.key] if cmd.key else ["<资源> <操作>"]
    for arg in cmd.positional_args:
        parts.append(_positional_tokens(cmd, arg)[0])
    if cmd.payload:
        parts.append("['<json>' | --from payload]")
    # `[--选项 ...]` 的出现与否**只看展示清单**（#334 / #323 验收追加）：`cmd.options` 里还有
    # 位置槽的 kebab 形态与兼容期旧名，用它判会出现「详情没有『选项：』段、用法行却宣称有
    # 选项」的同状态两种渲染（8 条：`dept|room|agent get`、`agent dm/wake`、`role get/rm`、
    # `container note`）。展示段与用法行必须同源，故统一用 `display_options`。
    if cmd.display_options:
        parts.append("[--选项 ...]")
    if cmd.payload and any(kind in ("array", "object") for kind in cmd.option_kinds.values()):
        parts.append("（复杂参数建议优先 --from payload）")
    return " ".join(parts)


def _alias_reverse(cmd: registry.ResolvedCommand) -> dict[str, tuple[str, ...]]:
    """「真参数名 → 别名元组」（help 展示用；旧下划线别名按 kebab 形态展示）。"""
    reverse: dict[str, tuple[str, ...]] = {}
    for alias, real in cmd.alias_map.items():
        reverse[real] = reverse.get(real, ()) + (cc.to_kebab(alias),)
    return reverse


def _param_sections(cmd: registry.ResolvedCommand) -> tuple[list[str], list[str]]:
    """参数清单拆成 `(位置行, 选项行)`：**先按声明的 `Arg`，再用未声明的 schema 参数补齐**。

    这是 C-08 / C-09 的正解（#323 口径第 3 版）：形态（位置 or 选项）、必填性、顺序
    全部由**声明**决定，schema 只提供 `kind` 与描述两样东西。

    - 位置行名字与用法行**同源**（`_positional_tokens`），不带 `--`，顺序 = 声明顺序；
    - 位置行的必填取 `arg.required`。**不能取 schema `required`**：工具签名里的参数
      全都有默认值 ⇒ `schema_required` 恒为空集 ⇒ `container note` 两个
      `Arg(required=True)` 的槽位被印成「（可选）」（C-09 现场）；
    - 未被声明到的 schema 参数按 schema 顺序补齐成选项行（它们的必填仍看 schema
      `required` —— 声明里没写，schema 是唯一来源）。
    """
    aliases = _alias_reverse(cmd)
    required_options = set(cmd.required_option_names)
    positionals = [
        cs.describe_positional(
            _positional_tokens(cmd, arg)[1],
            cmd.param_schemas.get(arg.target.name, {}),
            required=arg.required,
        )
        for arg in cmd.positional_args
    ]
    # 选项段直接吃 `display_options`（它已剔除位置槽，并已应用 `ConsoleCommand.exclude`
    # 的裁剪）；同一真名的多个形态（kebab / 兼容旧名 / 短别名）只出一行 —— 别名内联。
    options: list[str] = []
    seen: set[str] = set()
    for form in cmd.display_options:
        real = cmd.normalize_option(form)
        if real in seen:
            continue
        seen.add(real)
        options.append(
            cs.describe_param(
                real,
                cmd.param_schemas.get(real, {}),
                required=real in required_options,
                aliases=aliases.get(real, ()),
            )
        )
    return positionals, options


def _positional_note(cmd: registry.ResolvedCommand) -> str:
    """「位置参数：」段后面那句短注（C-08 需求 5：长括号说明收敛成一句）。

    只陈述两件解析层事实：值写在哪、**不接受**哪种写法。被拒绝的写法按同一批位置槽
    的 kebab 形态列出（`--dept-id` / `--container-id / --note`），与签名 token 一一对应。
    """
    rejected = " / ".join(f"--{cc.to_kebab(arg.target.name)}" for arg in cmd.positional_args)
    return f"写在命令名后面（见用法行），不接受 {rejected} 选项写法。"


def _help_detail_table() -> dict[str, str]:
    """全量详情表：一条命令一段（参数清单从工具 schema 派生）。

    不缓存：D2 开关可在进程内切换，而 help 的可用命令子集按运行时判定 —— 缓存会把
    「某一刻的可见集合」钉死。生成成本是纯字符串拼接（schema 反射本身按工具名缓存）。
    """
    table = registry.get_table()
    detail: dict[str, str] = {}
    for cmd in table.specs:
        lines = [_usage_line(cmd)]
        description = _tool_description(cmd)
        lines.append(f"用途：{description}" if description else "用途：（该工具未提供说明）")
        positionals, options = _param_sections(cmd)
        if positionals:
            lines.append("位置参数：")
            lines.extend(f"  {line}" for line in positionals)
            lines.append(f"  注：{_positional_note(cmd)}")
        if options:
            lines.append("选项：")
            lines.extend(f"  {line}" for line in options)
        lines.extend(cmd.help_lines)
        lines.extend(_HELP_OVERLAY.get(cmd.key, ()))
        detail[cmd.key] = "\n".join(lines)
    return detail


def __getattr__(name: str) -> Any:
    """把 `HELP_SUMMARY` / `HELP_DETAIL` 做成「访问时才生成」的模块属性（PEP 562）。

    C-a / C-c（设计 §4.1）：模块顶层的立即求值会逼 `consoleTool` 在 import 期拉起
    `tools`（49 个声明），而它被 `core` 在 `tools` 之前 import —— 那正是循环的入口。
    """
    if name == "HELP_SUMMARY":
        return _build_help_summary()
    if name == "HELP_DETAIL":
        return _help_detail_table()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _resource_help(resource: str, visible: Sequence[registry.ResolvedCommand]) -> str:
    """`help <资源>`：**每条子命令一行**（用法 + 用途），末尾给下一步。

    形参收 `Sequence` 而不是 `list`：上游 `registry.get_table().visible_specs()` 返回的就是
    `tuple[ResolvedCommand, ...]`（只读遍历，见 consoleRegistry.py:373），本函数只做 `for`
    遍历与过滤，不需要可变序列，也不该为了迁就注解在调用点 `list(...)` 造一次拷贝。

    need_fix_4 §七模块2.3：旧实现只印一行 `task 的可用操作：ls | get | create | update`，
    模型看得到操作名却不知道各自做什么，只能挨个 `help task <操作>` 试——一次试探就是
    一轮工具调用。用途直接从工具 docstring 派生（`_tool_description`），与 `help
    <资源> <操作>` 的「用途：」行同源，不会两处不一致。
    """
    lines: list[str] = [f"{resource} 的可用操作："]
    for cmd in visible:
        if cmd.resource != resource or not cmd.primary_action:
            continue
        description = _tool_description(cmd)
        line = _usage_line(cmd)
        lines.append(f"  {line} — {description}" if description else f"  {line}")
    lines.append(f"详情：help {resource} <操作>")
    return "\n".join(lines)


def _help_text(tokens: list[str]) -> dict[str, Any]:
    """`help` 的**唯一返回形态**：`{"success": True, "text": "<正文>"}`。

    不再返回 `"help"` 键（need_fix_4 §七模块2.1 / §六）：同一段正文既在 `text` 又在
    `help` 里，模型历史里就要为它付两遍 token；两个键还会随分支写法漂移（`help` 曾被
    当成"资源名标记"塞 `"mcp"`），语义已经冲突。正文只有一个字段，读它的地方只有一处。
    """
    table = registry.get_table()
    visible = table.visible_specs()
    visible_keys = {cmd.key for cmd in visible}
    if len(tokens) <= 1:
        return {"success": True, "text": _render_help(_build_help_summary(visible))}
    resource = tokens[1].lower()
    action = tokens[2].lower() if len(tokens) > 2 else ""
    # 扩展子树的 help 由各自的窄解析器提供（P3 才 extension 化）。
    if resource == "mcp":
        payload = mcp_console.help_payload(["mcp"] + tokens[2:])
        return {"success": True, "text": _render_help(str(payload.get("text") or ""))}
    if resource in ("openapi", "graphql"):
        payload = spec_console.help_payload([resource] + tokens[2:])
        return {"success": True, "text": _render_help(str(payload.get("text") or ""))}
    key = f"{resource} {action}".strip()
    if key in visible_keys:
        detail = _help_detail_table().get(key)
        if detail is not None:
            return {"success": True, "text": _render_help(detail)}
    available = [cmd.primary_action for cmd in visible if cmd.resource == resource and cmd.primary_action]
    if available:
        return {"success": True, "text": _render_help(_resource_help(resource, visible))}
    raise ConsoleError(
        "unknown_command",
        f"未知命令：'{key}'。可用资源：{' | '.join(table.resources)}",
    )


# ---------------------------------------------------------------------------
# 选项值收敛
#
# 选项的**类型**从 schema 派生（`ResolvedCommand.option_kinds`）：integer 走正整数校验、
# array 走 csv 拆分、object 走 JSON 解析、boolean 在解析层就是不带值的开关、其余原样
# 收下。这里只做「形态转换」，**不校验取值合法性**（枚举 / 业务规则归服务层）。
# ---------------------------------------------------------------------------


def _require_positive_int(raw: Any, *, what: str, command: str) -> int:
    """把位置参数 / 选项值收敛成正整数，否则报 `invalid_target`。

    只做「是不是正整数」这一件事，**不查库**（#187 裁定⑨：不建 `_resolve_task_id`）。
    为什么不能直接 `int(raw)`：`int()` 会接受 `" 187 "`、`"+187"`，也会为 `"0x1"` 抛
    `ValueError`、让 `int(True)` 得 1 —— 都不该被当成合法 id。
    """
    text = str(raw).strip()
    if not text.isdigit():
        raise ConsoleError(
            "invalid_target",
            f"`{command}` 的 {what} 必须是正整数，收到 {raw!r}。",
            example=f"{command} 187",
        )
    value = int(text)
    if value <= 0:
        raise ConsoleError(
            "invalid_target",
            f"`{command}` 的 {what} 必须大于 0，收到 {value}。",
            example=f"{command} 187",
        )
    return value


def _split_csv(raw: Any) -> list[str]:
    """逗号分隔的多值选项 → 去重保序的字符串列表（CSV 段 trim、空段丢弃）。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [segment for item in raw for segment in str(item).split(",")]
    else:
        parts = str(raw).split(",")
    return list(dict.fromkeys(p.strip() for p in parts if p.strip()))


_DEFAULT_ARRAY_ITEM_KINDS = frozenset({"string"})
"""调用方没传 schema 时的数组元素类型兜底（内部函数与历史用例的默认面）。"""


def _array_example(item_kinds: frozenset[str]) -> str:
    """该数组参数的示例字面量——与 help 形态注同源（`consoleSchema.array_shape_and_example`）。"""
    return cs.array_shape_and_example(item_kinds)[1]


_ARRAY_KIND_LABELS = {"string": "字符串", "integer": "整数", "object": "对象"}


def _array_kinds_label(item_kinds: frozenset[str]) -> str:
    """元素类型的可读名（报错文案用），顺序固定便于用例断言。"""
    order = [kind for kind in ("string", "integer", "object") if kind in item_kinds]
    return " / ".join(_ARRAY_KIND_LABELS.get(kind, kind) for kind in order) or "字符串"


def _csv_hint(name: str, item_kinds: frozenset[str]) -> str:
    """逗号分隔写法的可照抄形态；元素只收对象时没有 CSV 写法。"""
    if "string" in item_kinds:
        return f"--{name} a,b"
    if "integer" in item_kinds:
        return f"--{name} 1,2"
    return ""


def _coerce_csv_elements(
    text: str, *, name: str, command: str, item_kinds: frozenset[str]
) -> list[Any]:
    """CSV 段 → 元素列表：元素类型按 schema 收敛，整数逐项走正整数收敛。

    缺了这一步，`--depends-on 42,44` 会把 `['42','44']` 原样转给第 2 跳，被 schema
    校验拒成「应为 integer，实际收到 '42'」——这正是 need_fix_4 的 B1。
    """
    parts = [part.strip() for part in str(text).split(",") if part.strip()]
    if "string" in item_kinds:
        return parts
    if "integer" not in item_kinds:
        raise ConsoleError(
            "invalid_option_value",
            f"--{name} 的元素是对象，不能用逗号分隔的写法；"
            f"请写成 JSON 数组（如 '{_array_example(item_kinds)}'），或把配置放进 --from payload。",
            example=f"{command} --{name} '{_array_example(item_kinds)}'",
        )
    converted: list[Any] = []
    for part in parts:
        try:
            converted.append(
                _require_positive_int(part, what=f"--{name} 的元素", command=command)
            )
        except ConsoleError as exc:
            raise ConsoleError(
                "invalid_option_value",
                f"--{name} 的元素必须是正整数，收到 {part!r}。",
                example=f"{command} --{name} '{_array_example(item_kinds)}'",
            ) from exc
    return converted


def _validate_array_element(
    item: Any,
    *,
    index: int,
    name: str,
    command: str,
    item_kinds: frozenset[str],
) -> Any:
    """单个 JSON 数组元素按 schema 的元素类型校验并放行。

    `bool` 必须单独拦：`True/False` 是 `int` 的子类，放过去就变成 id `1/0`。
    """
    example = _array_example(item_kinds)

    def reject(reason: str) -> ConsoleError:
        return ConsoleError(
            "invalid_option_value",
            f"--{name} 的 JSON 数组第 {index} 个元素{reason}；"
            f"该参数的元素应为{_array_kinds_label(item_kinds)}（如 '{example}'）。",
            example=f"{command} --{name} '{example}'",
        )

    if isinstance(item, bool):
        raise reject("是布尔值（True/False 会被当成 1/0，这里不接受）")
    if isinstance(item, str):
        if "string" in item_kinds:
            return item
        raise reject("是字符串")
    if isinstance(item, int):
        if "integer" not in item_kinds:
            raise reject("是整数")
        if item <= 0:
            raise reject("不是正整数")
        return item
    if isinstance(item, dict):
        if "object" in item_kinds:
            return item
        raise reject("是对象")
    raise reject(f"的类型是 {type(item).__name__}")


def _parse_array_literal(
    text: str,
    *,
    name: str,
    command: str,
    item_kinds: frozenset[str] = _DEFAULT_ARRAY_ITEM_KINDS,
) -> list[Any]:
    """`[` 开头的值按 **JSON 数组字面量**解析：元素类型按 schema 校验，字符串元素不 trim。

    报错示例从 `array_shape_and_example` 取（不再写死 `["a","b"]`）：示例必须能照着
    写成功，否则错误提示本身就是第二个 bug（need_fix_4 §二 B2）。
    """
    example = _array_example(item_kinds)
    example_option = f"{command} --{name} '{example}'"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        csv_part = _csv_hint(name, item_kinds)
        csv_hint = f"，或改用逗号分隔（{csv_part}）" if csv_part else ""
        raise ConsoleError(
            "invalid_option_value",
            f"--{name} 的值以 `[` 开头，按 JSON 数组解析失败：{exc.msg}"
            f"（第 {exc.lineno} 行第 {exc.colno} 列）。"
            f"JSON 数组含双引号或空格时，外层请用单引号包裹（如 '{example}'）{csv_hint}；"
            f"复杂结构建议 --from payload。",
            example=example_option,
        ) from exc
    if not isinstance(parsed, list):
        raise ConsoleError(
            "invalid_option_value",
            f"--{name} 的值以 `[` 开头，但解析结果是 {type(parsed).__name__}，需要 JSON 数组。",
            example=example_option,
        )
    return [
        _validate_array_element(
            item, index=index, name=name, command=command, item_kinds=item_kinds
        )
        for index, item in enumerate(parsed)
    ]


def _explicit_empty_json_array(raw: Any) -> bool:
    """是否显式写了 JSON 空数组字面量（`'[]'` / `' [] '`）——B3 的空值分流依据。

    「空结果」有两种来源，语义相反：显式 `'[]'` 是「清空 / 一个都不要」，全空白是
    「这次没提供」。只看 `if not items` 会把两者混成一个 `None`，于是
    `task update <id> --depends-on '[]'` 变成 `no_update_action`（B3）。
    """
    segments = raw if isinstance(raw, (list, tuple)) else [raw]
    for segment in segments:
        if segment is None:
            continue
        text = str(segment).strip()
        if not text.startswith("["):
            continue
        try:
            if json.loads(text) == []:
                return True
        except json.JSONDecodeError:
            continue
    return False


def _split_option_values(
    raw: Any,
    *,
    name: str,
    command: str,
    item_kinds: frozenset[str] | None = None,
) -> list[Any]:
    """数组型选项原始值 → 元素列表（逐值段判断 `[` 形态，多次出现逐段合并）。

    `item_kinds` 缺省按 `string` 收（内部函数与历史用例的默认面），转发路径由
    `_convert_option` 从 schema 取出元素类型传进来。含 `object` 时**不去重**：`dict`
    不可 hash，且工作流节点数组允许同一成员跨节点出现（`[7, 8, 7]` 是合法流程）。
    """
    kinds = item_kinds or _DEFAULT_ARRAY_ITEM_KINDS
    segments = raw if isinstance(raw, (list, tuple)) else [raw]
    merged: list[Any] = []
    for segment in segments:
        if segment is None:
            continue
        text = str(segment).strip()
        if not text:
            continue
        if text.startswith("["):
            merged.extend(_parse_array_literal(text, name=name, command=command, item_kinds=kinds))
        else:
            merged.extend(_coerce_csv_elements(text, name=name, command=command, item_kinds=kinds))
    if "object" in kinds:
        return merged
    return list(dict.fromkeys(item for item in merged if item != ""))


def _convert_option(
    raw: Any, *, kind: str, name: str, command: str, prop: dict | None = None
) -> Any:
    """按 schema 声明的类型把选项值转成工具要的形态。

    `prop` 是该参数的 schema 片段，只有数组型用得到（元素类型决定怎么转、示例怎么写）。
    """
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        return cp.parse_boolean_value(str(raw), name=name, command=command)
    if kind == "integer":
        return _require_positive_int(raw, what=f"--{name}", command=command)
    if kind == "array":
        item_kinds = cs.array_item_kinds(prop or {})
        items = _split_option_values(raw, name=name, command=command, item_kinds=item_kinds)
        if items:
            return items
        # 显式 `'[]'` ⇒ []（能真清空依赖、能表达「不授权任何技能」）；纯空白 ⇒ None
        # （`--status ''` 仍 ≡ 未提供，#228 的契约不能在这条路上破掉）。
        return [] if _explicit_empty_json_array(raw) else None
    if kind == "object":
        text = str(raw).strip()
        if not text.startswith("{"):
            raise ConsoleError(
                "invalid_option_value",
                f"--{name} 是 object 型参数，值必须是 JSON object 字面量"
                f"（以 `{{` 开头，如 '{{\"k\":\"v\"}}'）；复杂结构建议放进 --from payload。",
                example=f"{command} --{name} '{{\"k\":\"v\"}}'",
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConsoleError(
                "invalid_option_value",
                f"--{name} 需要 JSON object，解析失败：{exc.msg}"
                f"（第 {exc.lineno} 行第 {exc.colno} 列）。",
                example=f"{command} --{name} '{{\"k\":\"v\"}}'",
            ) from exc
        if not isinstance(parsed, dict):
            raise ConsoleError(
                "invalid_option_value",
                f"--{name} 需要 JSON object，收到 {type(parsed).__name__}。",
            )
        return parsed
    if kind == "string":
        if cs._extract_enum(prop or {}) and not str(raw).strip():
            # #228 契约（`--status ''` ≡ 未提供）在枚举参数上的落地：'' 不是合法枚举值，
            # 直接透传会被 schema 的 Literal 拒成 invalid_tool_args，而用户本意是"没提供"。
            # 非枚举字符串**不**这么做：`--note ''` 是「把备注清空」的合法写法。
            return None
        return raw
    return raw


def _option_args(cmd: registry.ResolvedCommand, options: dict[str, Any]) -> dict[str, Any]:
    """已解析选项 → 转发参数（别名归一 + 按类型转换）。"""
    args: dict[str, Any] = {}
    for name, raw in options.items():
        real = cmd.normalize_option(name)
        kind = cmd.option_kinds.get(real) or cmd.option_kinds.get(name) or "string"
        args[real] = _convert_option(
            raw,
            kind=kind,
            name=cc.to_kebab(real),
            command=cmd.key,
            prop=cmd.param_schemas.get(real),
        )
    return args


# ---------------------------------------------------------------------------
# 四步流水线（设计 §5.1）
# ---------------------------------------------------------------------------


def _label_for(cmd: registry.ResolvedCommand, arg: registry.Arg) -> str:
    """位置槽在报错文案里的称呼（`<id>` / `<名称>` / `<参数名>`）。"""
    kind = cmd.param_kinds.get(arg.target.name, "string")
    effective = arg.target.type or (int if kind == "integer" else str)
    if effective is int:
        return "<id>"
    if len(cmd.positional_args) == 1:
        return "<名称>"
    return f"<{arg.target.name}>"


def _positional_rewrite_hint(cmd: registry.ResolvedCommand, arg: registry.Arg) -> str:
    """位置参数写错时的改写提示（按「能不能按名定位」分流，§5.2 / 变化 e）。"""
    kind = cmd.param_kinds.get(arg.target.name, "string")
    effective = arg.target.type or (int if kind == "integer" else str)
    if effective is not int:
        return ""
    if arg.required:
        list_command = next(
            (c.key for c in cmd.tool.spec.console if c.primary_action == "ls"),  # type: ignore[union-attr]
            f"{cmd.resource} ls",
        )
        return (
            f" 位置参数只接受正整数 ID（不接受名称）；按名称查询请先用 `{list_command}` 取 ID。"
        )
    return f" 按名称操作请改用 `--name <名称>`（位置参数与 --name 二选一，同时给出时以 <id> 为准）。"


def _resolve_positionals(
    cmd: registry.ResolvedCommand, tokens: list[str], options: dict[str, Any] | None = None
) -> tuple[dict[str, Any], str | None]:
    """第 2 步：位置 token → `pos_args` + 尾部内联 payload token。"""
    positional_args = cmd.positional_args
    min_pos = cmd.min_pos
    max_pos = len(positional_args)
    allowed_max = max_pos + 1 if cmd.payload else max_pos
    if len(tokens) < min_pos or len(tokens) > allowed_max:
        expected = (
            f"位置参数 + 可选内联 JSON，共 {max_pos}~{max_pos + 1} 段"
            if cmd.payload
            else f"{min_pos}~{max_pos} 个位置参数"
        )
        raise ConsoleError(
            "invalid_argument_count",
            f"`{cmd.key}` 需要 {expected}，收到 {len(tokens)} 段"
            + _locator_rewrite_hint(cmd, options or {})
            + "。",
            example=f"help {cmd.key}",
        )
    pos_args: dict[str, Any] = {}
    for index, token in enumerate(tokens[:max_pos]):
        arg = positional_args[index]
        name = arg.target.name
        kind = cmd.param_kinds.get(name, "string")
        effective = arg.target.type or (int if kind == "integer" else str)
        if effective is int:
            try:
                pos_args[name] = _require_positive_int(
                    token, what=f"位置参数 {_label_for(cmd, arg)}", command=cmd.key
                )
            except ConsoleError as exc:
                raise ConsoleError(
                    exc.code,
                    exc.message + _positional_rewrite_hint(cmd, arg),
                    **exc.extra,
                ) from exc
        else:
            pos_args[name] = token
    inline_payload = tokens[max_pos] if len(tokens) > max_pos else None
    return pos_args, inline_payload


def _locator_rewrite_hint(cmd: registry.ResolvedCommand, options: dict[str, Any]) -> str:
    """位置槽参数被写成 `--选项` 时的**等价改写示例**（#220 需求 1，不扩错误码面）。

    为什么在报错里给改写而不是放行：清单 §A7 实测 `dept get --dept-id 7` 是第一次
    使用必踩的坑——help 把位置参数标成「不接受选项写法」，照着敲却被
    `invalid_argument_count` 拒，而报错只说「收到 0 个」，一个字不提怎么改。
    这里只做「说清楚」：语义不放宽，错误码也不扩。

    值取**首个**出现的定位选项（`--dept-id 7 --name x` 同时给出时提示改第一个即可，
    报错文案不承诺处理第二种写法）。
    """
    positional = cmd.positional_param_names
    if not positional:
        return ""
    for name, raw in options.items():
        real = cmd.normalize_option(name)
        if real not in positional:
            continue
        # 文案单一来源在解析层（`consoleParsing.positional_option_hint`）：C-01 之后同一句
        # 提示要同时出现在「位置个数不足」与「位置槽被写成 --选项」两条路上，两处各写一份
        # 必然漂移。
        return cp.positional_option_hint(cmd, name, str(raw))
    return ""


def _payload_key_whitelist(cmd: registry.ResolvedCommand) -> frozenset[str]:
    """配置输入（payload / 内联 JSON）允许出现的键：工具真名，非位置槽、未被 exclude。

    与 `--选项` 的白名单同源：`command_options_map` 是命令行形态，这里是**真名**集合
    （`_validate_payload_keys` 会先用 `normalize_option` 归一，所以 kebab / 旧下划线名 /
    别名写进 payload 也能正确落位）。
    """
    return frozenset(
        name
        for name in cmd.param_names
        if name not in cmd.positional_param_names and name not in cmd.excluded_param_names
    )


def _validate_payload_keys(cmd: registry.ResolvedCommand, payload_args: dict[str, Any]) -> dict[str, Any]:
    """按**命令参数白名单**校验配置输入的键名（C-03① / #334），返回归一成真名的字典。

    改前这里只校验了 payload 根节点是 object，未知键会一路漏到 `func(**args)` 的
    Python `TypeError`（错误体 `error_code: null`）——入口不做校验，问题推到第 2 跳才炸。
    现在两类键在这里就被结构化拒绝：

    - **未知键** ⇒ `unknown_payload_key`（附该命令接受的键清单）；
    - **位置参数键** ⇒ `positional_payload_key`（定位参数只能写成位置参数形态，不能塞进
      配置输入——否则与位置槽/选项三处来源打架，正是 C-01 要消的「同一事实多来源」）。
    """
    if not payload_args:
        return payload_args
    allowed = _payload_key_whitelist(cmd)
    positional = cmd.positional_param_names
    unknown: list[str] = []
    positional_keys: list[str] = []
    normalized: dict[str, Any] = {}
    for key, value in payload_args.items():
        real = cmd.normalize_option(key)
        if real in positional:
            positional_keys.append(key)
        elif real in allowed:
            normalized[real] = value
        else:
            unknown.append(key)
    if unknown:
        raise ConsoleError(
            "unknown_payload_key",
            f"配置输入里有 `{cmd.key}` 不认识的键：{' / '.join(unknown)}。"
            f"该命令接受：{' / '.join(sorted(allowed)) or '（无）'}。",
            example=f"help {cmd.key}",
            unknown=unknown,
            allowed=sorted(allowed),
        )
    if positional_keys:
        raise ConsoleError(
            "positional_payload_key",
            f"配置输入里不能给位置参数：{' / '.join(positional_keys)}。"
            f"`{cmd.key}` 的定位参数要写在命令名后面（位置参数形态，见 `help {cmd.key}`）。",
            example=f"help {cmd.key}",
            positional=positional_keys,
        )
    return normalized


def _resolve_payload(
    cmd: registry.ResolvedCommand,
    inline_payload: str | None,
    input_source: str | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """第 3 步的整包配置输入部分（`'<json>'` / `--from payload` 的唯一入口）。"""
    if not cmd.payload:
        if input_source is not None or payload is not None:
            raise ConsoleError(
                "ambiguous_input_source",
                f"`{cmd.key}` 不接受配置输入，请去掉 payload 字段。",
                example=cmd.key,
            )
        return {}
    config_tokens = [] if inline_payload is None else [inline_payload]
    raw = cp._resolve_input_payload(
        config_tokens, payload, input_source=input_source, command_key=cmd.key
    )
    return _validate_payload_keys(cmd, raw)


def _check_conflicts(
    cmd: registry.ResolvedCommand,
    pos_args: dict[str, Any],
    opt_args: dict[str, Any],
    payload_args: dict[str, Any],
) -> None:
    """冲突校验（禁止静默覆盖）：位置槽 / `--选项` / `payload` 三者互斥。

    除了原来的「**同名**重叠」，还判「**同一定位语义的多来源**」（`cmd.locators`，C-16 /
    #334）：`dept rm 2 --name 研发部` 是位置 `<id>` 与 `--name` 两个**不同参数名**，同名
    重叠判不出来，而底层 `if dept_id is not None` 会静默丢掉 `--name` —— 只能给一个。
    """
    duplicated = sorted(set(opt_args) & set(payload_args))
    if duplicated:
        raise ConsoleError(
            "ambiguous_input_source",
            f"这些参数同时出现在 --选项 与配置输入中：{' / '.join(duplicated)}。请二选一。",
            example=f"help {cmd.key}",
        )
    for source_name, source in (("--选项", opt_args), ("配置输入", payload_args)):
        overlap = sorted(set(pos_args) & set(source))
        if overlap:
            raise ConsoleError(
                "ambiguous_input_source",
                f"位置参数 {' / '.join(overlap)} 不能与{source_name}重复提供；请二选一。",
                example=f"help {cmd.key}",
            )
    locators = cmd.locators
    if locators:
        provided = [
            name for name in locators if name in pos_args or name in opt_args or name in payload_args
        ]
        if len(provided) > 1:
            raise ConsoleError(
                "ambiguous_input_source",
                f"`{cmd.key}` 的定位参数 {' / '.join(provided)} 只能给一个（二选一，不能同时提供）。",
                example=f"help {cmd.key}",
            )


def _check_required(cmd: registry.ResolvedCommand, call_args: dict[str, Any]) -> None:
    """第 4 步：必填选项校验（缺失报 `missing_required_option`）。"""
    missing = [name for name in cmd.required_option_names if name not in call_args]
    if missing:
        raise ConsoleError(
            "missing_required_option",
            f"`{cmd.key}` 缺少必填参数：{' / '.join('--' + cc.to_kebab(name) for name in missing)}"
            f"（也可用配置输入整包提供）。",
            example=f"help {cmd.key}",
            missing=missing,
        )


def _build_call_args(
    cmd: registry.ResolvedCommand, rest: list[str], payload: dict[str, Any] | None
) -> dict[str, Any]:
    """四步流水线的**同步唯一实现**：解析 → 位置槽 → 选项 → payload → 冲突/必填 → 工具入参。

    为什么单独抽出来：`_dispatch`（真执行）与 `console_triggers_self_interrupt`
    （自中断谓词）都必须跑完这四步才能回答「这条命令会不会走到转发」。两处各抄一份的话，
    一旦漂移就会出现「谓词说会中断、真执行却在选项层失败」的**假中断标记**——正是 #165 A
    那类缺陷。所以这里只允许有一份实现，谓词与执行都调用它，判据钉「`cp._parse_options`
    在本模块只出现一次」。
    """
    # `ResolvedCommand` 结构化满足解析层声明的 `CommandSpecLike`（见 `consoleParsing`），
    # 所以这里直接传，静态检查无需 cast；真正的约束由 `consoleRegistry.build()` 的
    # fail-fast 校验兜住（选项面不合规的命令根本建不出表）。
    positional, options, input_source = cp._parse_options(cmd, rest)
    pos_args, inline_payload = _resolve_positionals(cmd, positional, options)
    opt_args = _option_args(cmd, options)
    payload_args = _resolve_payload(cmd, inline_payload, input_source, payload)
    _check_conflicts(cmd, pos_args, opt_args, payload_args)
    call_args = {**pos_args, **payload_args, **opt_args}
    _check_required(cmd, call_args)
    return call_args


def _dispatch(cmd: registry.ResolvedCommand, rest: list[str], payload: dict[str, Any] | None) -> NoReturn:
    """四步流水线 → `ForwardToolCall`（同步抛出，控制流不经过 await）。"""
    if not registry.command_console_visible(cmd):
        raise ConsoleError(
            "unknown_command",
            f"命令当前不可用：`{cmd.key}`（对应工具已被环境开关停用）。",
        )
    raise ForwardToolCall(cmd.tool_name, _build_call_args(cmd, rest, payload))


# ---------------------------------------------------------------------------
# 动态自中断判定（#154 D6）
# ---------------------------------------------------------------------------

def console_triggers_self_interrupt(tool_call: Any) -> bool:
    """`console` 本次调用是否会自中断当前 turn。

    `AgentTurnRunner` 用它在 handler 执行**之前**决定要不要先写 `SELF_INTERRUPT` 标记。
    **必须有这个判定器**：静态 `self_interrupt=True` 会让每条 console 命令都留下中断
    标记；静态 `False` 则让 `reload` 丢掉标记，重启后被重放成第二次重载。

    判定口径：只要这条命令能成功执行到转发，它就会中断。因此这里**必须走完整的解析
    链路**（分词 → 路由 → 四步流水线的同步部分），不能只看命令名 —— `reload --bogus`
    会在选项层失败、根本到不了转发，只看名字会留下**假的中断标记**。

    任何解析异常都返回 False（不中断）——判定器在 turn runner 的调用链上，抛异常会把
    一次普通管理命令变成 turn 崩溃，代价远大于漏判。
    """
    try:
        raw_args = tool_call.function_args
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        command = args.get("command")
        if not isinstance(command, str):
            return False
        tokens = cp._tokenize(command)
        # `--help` / `-h` 在 `console()` 里是短路返回，永远不会走到转发 ⇒ 不会中断。
        if any(token in cp.HELP_FLAGS for token in tokens):
            return False
        if not tokens or tokens[0].lower() in ("help", "?"):
            return False
        if tokens[0].lower() in ("mcp", "openapi", "graphql"):
            # 扩展子树的自中断语义各自持有（P3 接入前按「不中断」处理，与本轮行为一致）。
            return False
        table = registry.get_table()
        cmd, _err = table.resolve(tokens)
        if cmd is None or cmd.spec is None or not cmd.spec.self_interrupt:
            return False
        payload = args.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return False
        rest = tokens[1:] if not cmd.actions else tokens[2:]
        # 与真执行共用同一份流水线（`_build_call_args`）：解析失败会抛，被下面兜住 ⇒ False。
        _build_call_args(cmd, rest, payload)
        return True
    except Exception:  # noqa: BLE001 —— 判定器绝不把异常抛进 turn runner
        return False


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

@tool(visibility=ToolVisibility.TOOL_ONLY, self_interrupt_predicate=console_triggers_self_interrupt)
async def console(command: str, payload: Optional[dict] = None, _context: ToolCallContext = None) -> dict:
    """团队/组织/成员/房间/角色模板的管理控制台，用命令行完成管理操作。

    Args:
        command: 要执行的命令。资源名：dept(部门) / agent(成员) / room(房间) / role(角色模板) / task(协作任务) / container(容器)。示例：'dept ls'、'agent create --from payload'、'dept get 7'、'task ls --status TODO'、'task update 187 --remove-field reviewer_id'、'container note a1b2c3d4e5f6 "跑前端构建"'。输入 'help' 查看全部命令；任意命令加 --help / -h 可看该命令的参数清单。
        payload: 仅当 command 使用 --from payload 时提供，作为 create/update 的 JSON 配置对象；其他命令不得提供。
    """
    try:
        tokens = cp._tokenize(command)
    except ConsoleError as exc:
        return cp._error_payload(exc)

    # 扩展子树整体交给各自的窄解析器（三层子命令 / 双位置参数 / `name=value` 只属于
    # mcp / openapi / graphql）。P3 接 ConsoleExtension 前保持这条硬分派，语义零变化。
    if tokens and tokens[0].lower() == "mcp":
        return await mcp_console.dispatch(command, tokens, _context)
    if tokens and tokens[0].lower() in ("openapi", "graphql"):
        return await spec_console.dispatch(command, tokens, _context)

    # 全局 `--help` / `-h`（D12）：任何位置命中即短路返回该命令的 help，不占选项白名单。
    if any(token in cp.HELP_FLAGS for token in tokens):
        help_tokens = [token for token in tokens if token not in cp.HELP_FLAGS]
        try:
            return _help_text(["help"] + help_tokens[:2])
        except ConsoleError as exc:
            return cp._error_payload(exc)

    if tokens and tokens[0].lower() in ("help", "?"):
        try:
            return _help_text(tokens)
        except ConsoleError as exc:
            return cp._error_payload(exc)

    table = registry.get_table()
    cmd, err = table.resolve(tokens)
    if cmd is None:
        return {
            "success": False,
            "error_code": "unknown_command",
            "message": err or "未知命令",
            "resources": list(table.resources),
        }

    try:
        rest = tokens[1:] if not cmd.actions else tokens[2:]
        return _dispatch(cmd, rest, payload)
    except ConsoleError as exc:
        return cp._error_payload(exc)
    except ForwardToolCall:
        # 转发信号必须原样上抛（#160）：它是控制流，不是业务失败。
        # 这个 `except` 必须排在下面的 `except Exception` **之前**。
        raise
    except Exception as exc:  # 业务异常透传（draft §6）
        return error_result(exc)

