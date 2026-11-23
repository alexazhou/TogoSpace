"""`console` 工具：命令行入口，只做**收集与拼接**（设计 §5）。

设计依据：`docs/design/2026-11-20-console-tool-metadata/console_tool_metadata_design.md`。
本模块现在**不认识任何具体工具名**：

| 旧实现 | 现实现 |
| --- | --- |
| `consoleCommands.COMMAND_SPECS`（手抄命令表） | `consoleRegistry.get_table()`（由 `@tool(...)` 派生） |
| 5 个 verb 处理器（LIST / GET / SAVE / DELETE / DIRECT） | **一条四步流水线**（§5.1） |
| `_HELP_OVERLAY` 按 key 手抄 + 各命令的定位特判 | help 纯拼接：用法/用途/参数全部派生，只补 `ToolSpec.help` / `ConsoleCommand.help` |

保留的手写部分只剩三类，都是「命令表里推不出来的东西」：
1. **全局用法说明**（重复选项、CSV / JSON 等价、`--from payload` 契约）；
2. **错误格式**（`ConsoleError` 的 code / message / example）；
3. **类型转换**（正整数收敛、CSV 拆分、JSON 字面量解析、布尔解析）。

`_HELP_OVERLAY` 仍是手写补充说明（P2 #317 已逐条收拢过）：**它的判定口径、剩余条目
为什么不搬进 `@tool(help=...)` / `ConsoleCommand(help=...)`、以及「复述」按什么口径判
—— 全部见该常量定义处的注释**，本模块与 `toolSpec` 都不再讲第二遍。
"""

from __future__ import annotations

import json
from typing import Any, NoReturn, Optional

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


#: 手写补充说明：**只放「命令级事实」** —— 参数清单 / 用法行 / 用途行里推不出来的那些。
#:
#: 判定口径（P2 收拢时逐条过的，来源：设计 §5.3 + 审计报告 §7 + #317 报告 §2）：
#: 1. **参数清单已经标注的**（必填/可选、参数名、类型、形态、别名、参数级语义）⇒ 不复述。
#:    「是否重复」以**渲染出来的 help 文本**为准，不是以语义等价为准：例如 `dept create`
#:    的四个必填参数在清单里已标（必填），overlay 再写「新建必须给 --name / …」就是把同一
#:    件事说两遍；反过来 `dept update` / `agent update` 的数组参数描述只说「省略表示保持
#:    现值」，「出现即全量替换」这句在它们的渲染文本里并没有出现过，所以保留。
#: 2. **命令级语义**（错误码、服务层拒绝条件、PATCH 之外的副作用）⇒ 保留：schema 里没有
#:    对应字段，命令表也推不出来。
#: 3. **跨命令指引**（「改已有 X 请用 Y」）⇒ 保留：它描述的是命令之间的关系，不属于任何
#:    单条 schema。
#: 4. **全局规则**（重复选项、CSV / JSON 等价）⇒ 正本在 `help` 摘要（见
#:    `_build_help_summary`），本表**不再重复**。
#:    例外：判定不是纯语义题，还要看**已有契约**——`task update` 的「数组型选项可重复
#:    出现 / 标量重复报 duplicate_option」虽与摘要重复，但 `test_console_meta` 钉着它
#:    必须出现在该命令的 help 里（#187-④），删了就红；同理 `task create` 的「返回体
#:    hint 会再提醒一次」被 `test_console_usability_pack` 钉着。这类冲突以用例为准，
#:    要删先改用例（本单不碰）。
#:
#: 剩余条目为什么不搬进 `@tool(help=...)` / `ConsoleCommand(help=...)`：设计 §5.3 给了那个
#: 归宿，但剩下这些要么横跨同一工具的多条命令（`dept ls` / `dept get` 共用
#: `get_dept_info`），要么描述服务层行为（`DEPT_HAS_CHILDREN`、`no_update_action`），
#: 按「命令 key 索引」放在一张表里比拆散进十几处工具声明更贴它们的实际归属。两种落点对
#: help 输出没有差别（同一段文本换个存放位置，渲染顺序也不变），所以本单没有为此搬迁：
#: 搬到 `@tool(help=...)` 要改十几处工具声明，收益只是「按设计放到名义上更对的地方」。
#:
#: **只许放「补充」，不许放参数清单**：放进去就等于又抄了一份事实。
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
    "agent get": (
        "按名字查询走运行时列表：离职成员报 member_not_on_board，在职未加载报 agent_runtime_not_loaded。",
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
    if cmd.options:
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


def _positional_lines(cmd: registry.ResolvedCommand) -> list[str]:
    """「位置参数：」段的行（方案 B / #323）：名字逐字复用签名 token，不带 `--`。

    必填/可选按**声明**（`Arg.required`）判，**不按 schema 的 `required`** —— C-09 的
    根因就是拿了 schema required：工具签名里的参数全都有默认值，`schema_required`
    因此恒为空集，`container note` 两个 `Arg(required=True)` 的槽位被印成「（可选）」。
    """
    lines: list[str] = []
    for arg in cmd.positional_args:
        name = arg.target.name
        token = _positional_tokens(cmd, arg)[1]
        prop = cmd.param_schemas.get(name, {})
        # 必填取**声明**（`Arg.required`），不取 schema 的 `required`：工具签名参数全有
        # 默认值 ⇒ `schema_required` 恒空集 ⇒ 一条都标不出来（C-09 的根因）。
        lines.append(cs.describe_positional(token, prop, required=arg.required))
    return lines


def _positional_note(cmd: registry.ResolvedCommand) -> str:
    """「位置参数：」段后面那句短注（C-08 需求 5：长括号说明收敛成一句）。

    只陈述两件解析层事实：值写在哪、**不接受**哪种写法。被拒绝的写法按同一批位置槽
    的 kebab 形态列出（`--dept-id` / `--container-id / --note`），与签名 token 一一对应。
    """
    rejected = " / ".join(f"--{cc.to_kebab(arg.target.name)}" for arg in cmd.positional_args)
    return f"写在命令名后面（见用法行），不接受 {rejected} 选项写法。"


def _describe_params(cmd: registry.ResolvedCommand) -> list[str]:
    """「选项：」段的行：schema 顺序 + 别名（全部派生）。

    跳过位置槽绑定的参数（它们进「位置参数：」段）；其余（schema 顺序）都是选项。
    选项的必填仍按 schema `required`（与改动前一致，本单只动位置参数的判据）。
    """
    aliases = _alias_reverse(cmd)
    required = set(cmd.schema_required)
    skip = cmd.positional_param_names
    return [
        cs.describe_param(name, prop, required=name in required, aliases=aliases.get(name, ()))
        for name, prop in cmd.param_schemas.items()
        if name not in skip
    ]


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
        positionals = _positional_lines(cmd)
        if positionals:
            lines.append("位置参数：")
            lines.extend(f"  {line}" for line in positionals)
            lines.append(f"  注：{_positional_note(cmd)}")
        options = _describe_params(cmd)
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


def _help_text(tokens: list[str]) -> dict[str, Any]:
    table = registry.get_table()
    visible = table.visible_specs()
    visible_keys = {cmd.key for cmd in visible}
    if len(tokens) <= 1:
        return {"success": True, "help": _render_help(_build_help_summary(visible))}
    resource = tokens[1].lower()
    action = tokens[2].lower() if len(tokens) > 2 else ""
    # 扩展子树的 help 由各自的窄解析器提供（P3 才 extension 化）。
    if resource == "mcp":
        payload = mcp_console.help_payload(["mcp"] + tokens[2:])
        return {"success": True, "help": _render_help(str(payload.get("text") or ""))}
    if resource in ("openapi", "graphql"):
        payload = spec_console.help_payload([resource] + tokens[2:])
        return {"success": True, "help": _render_help(str(payload.get("text") or ""))}
    key = f"{resource} {action}".strip()
    if key in visible_keys:
        detail = _help_detail_table().get(key)
        if detail is not None:
            return {"success": True, "help": _render_help(detail)}
    available = [cmd.primary_action for cmd in visible if cmd.resource == resource and cmd.primary_action]
    if available:
        return {"success": True, "help": f"{resource} 的可用操作：" + " | ".join(available)}
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


def _parse_array_literal(text: str, *, name: str, command: str) -> list[str]:
    """`[` 开头的值按 **JSON 数组字面量**解析（#219）：元素必须是字符串、不 trim。"""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConsoleError(
            "invalid_option_value",
            f"--{name} 的值以 `[` 开头，按 JSON 数组解析失败：{exc.msg}"
            f"（第 {exc.lineno} 行第 {exc.colno} 列）。"
            f"要么写成合法的 JSON 数组（如 '[\"a\",\"b\"]'），要么改用逗号分隔"
            f"（--{name} a,b），或把配置放进 --from payload。",
            example=f"{command} --{name} '[\"a\",\"b\"]'",
        ) from exc
    if not isinstance(parsed, list):
        raise ConsoleError(
            "invalid_option_value",
            f"--{name} 的值以 `[` 开头，但解析结果是 {type(parsed).__name__}，需要 JSON 数组。",
            example=f"{command} --{name} '[\"a\",\"b\"]'",
        )
    items: list[str] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, str):
            raise ConsoleError(
                "invalid_option_value",
                f"--{name} 的 JSON 数组第 {index} 个元素是 {type(item).__name__}，"
                f"这里只接受字符串元素（如 '[\"a\",\"b\"]'）。",
                example=f"{command} --{name} '[\"a\",\"b\"]'",
            )
        items.append(item)
    return items


def _split_option_values(raw: Any, *, name: str, command: str) -> list[str]:
    """数组型选项原始值 → 去重保序列表（逐值段判断 `[` 形态，多次出现逐段合并）。"""
    segments = raw if isinstance(raw, (list, tuple)) else [raw]
    merged: list[str] = []
    for segment in segments:
        if segment is None:
            continue
        text = str(segment).strip()
        if not text:
            continue
        if text.startswith("["):
            merged.extend(_parse_array_literal(text, name=name, command=command))
        else:
            merged.extend(_split_csv(text))
    return list(dict.fromkeys(item for item in merged if item))


def _convert_option(raw: Any, *, kind: str, name: str, command: str) -> Any:
    """按 schema 声明的类型把选项值转成工具要的形态。"""
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        return cp.parse_boolean_value(str(raw), name=name, command=command)
    if kind == "integer":
        return _require_positive_int(raw, what=f"--{name}", command=command)
    if kind == "array":
        return _split_option_values(raw, name=name, command=command) or None
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
    return raw


def _option_args(cmd: registry.ResolvedCommand, options: dict[str, Any]) -> dict[str, Any]:
    """已解析选项 → 转发参数（别名归一 + 按类型转换）。"""
    args: dict[str, Any] = {}
    for name, raw in options.items():
        real = cmd.normalize_option(name)
        kind = cmd.option_kinds.get(real) or cmd.option_kinds.get(name) or "string"
        args[real] = _convert_option(raw, kind=kind, name=cc.to_kebab(real), command=cmd.key)
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
        text = str(raw).strip()
        quoted = f"'{text}'" if (not text or " " in text) else text
        return (
            f"。注意：--{cc.to_kebab(name)} 是定位参数，`{cmd.key}` 的定位只接受位置参数形态"
            f"（不接受 --选项 写法）；等价写法：`{cmd.key} {quoted}`"
            f"（其余 --选项 照原样保留在命令尾部）"
        )
    return ""


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
    return cp._resolve_input_payload(
        config_tokens, payload, input_source=input_source, command_key=cmd.key
    )


def _check_conflicts(
    cmd: registry.ResolvedCommand,
    pos_args: dict[str, Any],
    opt_args: dict[str, Any],
    payload_args: dict[str, Any],
) -> None:
    """冲突校验（禁止静默覆盖）：位置槽 / `--选项` / `payload` 三者互斥。"""
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

