"""`console` 工具的路由与 help 文本。

与 `consoleParsing` 的分工：那边只管「命令长什么样」（解析、输入来源），
这边只管「解析完的命令怎么落到原工具上」。分开是为了让转发口径能被单独变异测试
（#154 CC2）——把这里的路由改坏不该影响解析与 token 校验。

**权限不在这里算**（#160）：每条子命令的处理方式是把「目标工具名 + 参数」装进
`ForwardToolCall` 抛出去，由唯一执行入口 `AgentToolRegistry.execute_tool_call`
在第 2 跳做鉴权与执行。所以同一个工具「直接调」和「经 console 调」永远走同一份
权限判定，不会出现「原工具调不了、console 却能调」的错位。

## #196 P2：从「每条命令一段 if」收敛成「每种动作语义一个处理器」

改造前 `_route` 是一条 200 行的 `if key == "dept rm":` 长链：定位、补丁、预演、
转发参数组装每条命令各写一遍。同一种语义出现 5 次就会漂移 5 次 —— 于是只能靠
「数 `_resolve_*` 调用次数」那种结构测试去兜症状。现在语义提到命令表上
（`CommandSpec.verb`：GET / LIST / SAVE / DELETE / DIRECT），处理器只有 5 个：

| verb | 做什么 | 例子 |
| --- | --- | --- |
| GET | 定位 + 转发（不改库） | `dept get root`、`task get 187` |
| LIST | 只转发选项 | `dept ls`、`task ls --status TODO` |
| SAVE | 输入 + 选项 + 定位，交给原工具的 PATCH 语义 | `dept update root '{...}'` |
| DELETE | 定位 + 转发 | `dept rm root --recursive` |
| DIRECT | 位置参数就是工具参数 | `agent wake bob`、`reload` |

**PATCH 语义在原工具里**（已下沉到 `save_dept` / `save_agent` / `save_room` /
`save_role_template`）：省略字段 = 保持现值。所以 console 侧不再读库拼补丁，直接
把「出现了哪些键」原样转过去；`console*` 四个模块不再依赖 `dal`。

## help 也是派生的

`HELP_SUMMARY` / `HELP_DETAIL` 由命令表 + `consoleSchema` 生成，手写的只剩
「参数清单之外的一句话补充」（`_HELP_OVERLAY`：PATCH 语义提示、`--remove-field`
候选占位符、重复选项只最后一个生效……）。加一条新命令只改命令表一处：help 行、
参数清单、选项白名单、布尔开关判定全部自动跟随。
"""

from __future__ import annotations

import functools
import json
from typing import Any, Callable, Mapping, NoReturn, Optional, Sequence

from constants import ALLOWED_CLEAR_FIELDS
from exception import ForwardToolCall
from service.roomService import ToolCallContext
from toolResult import error_result

from . import consoleCommands as cc
from . import consoleParsing as cp
from . import consoleSchema as cs
from .consoleParsing import ConsoleError, ParsedCommand

# ---------------------------------------------------------------------------
# help 文本（#154 CC6 要实测它的体积）
#
# 刻意压到最短：只列 资源/操作 + 三类输入的一句话说明。详细参数放到
# `help <资源> <操作>`，模型按需再查 —— 这比一次性把所有参数灌进 context 省得多，
# 也是 draft §5 的设计意图。
#
# **摘要与详情都由命令表派生**（#196 P2）：手抄的摘要与命令表之间没有机械约束，
# 加了命令忘记加 help 行也不会让任何用例变红，只能靠人记得。
# ---------------------------------------------------------------------------


def _visible_command_specs() -> list[cc.CommandSpec]:
    """当前可通过 console 使用的命令（运行时按 `console_visible` 过滤）。"""
    return [spec for spec in cc.COMMAND_SPECS if cc.command_console_visible(spec)]


def _build_help_summary(specs: Optional[Sequence[cc.CommandSpec]] = None) -> str:
    """一行一个资源，操作按命令表顺序拼接（摘要里**不出现**参数清单）。

    `specs` 为空时用完整命令表（模块级 `HELP_SUMMARY` 的静态形态，供结构测试使用）；
    `help` 输出则在运行时传入可见命令子集，避免把 D2 开关状态烘焙进 import 期常量。

    形参类型是 `Sequence` 而不是 `list`：命令表本身是 `tuple`，用 `list` 会让
    「默认取整张表」这一支与形参声明冲突（#205 修的 mypy error）。这里只遍历、
    不修改，`Sequence` 已足够表达所需能力。
    """
    specs = cc.COMMAND_SPECS if specs is None else specs
    grouped: dict[str, list[cc.CommandSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.resource, []).append(spec)
    lines: list[str] = []
    # 对齐宽度按**实际资源名**算，不写死 8：`container` 是 9 个字符，写死 8 会把它和
    # 操作名粘成 `containernote`（#203 实测），而粘上之后结构测试按 token 切词就取不到
    # 这一行。动态算也让以后加更长的资源词不必回来改这里。
    width = max((len(resource) for resource in grouped), default=0) + 2
    for resource, resource_specs in grouped.items():
        actions = "|".join(spec.action for spec in resource_specs if spec.action)
        lines.append(f"{resource:<{width}}{actions}".rstrip())
    lines.append("用法：<资源> <操作> [位置参数] [--选项 ...]")
    lines.append("create/update 的配置输入（可选）：'<json>' 或 --from payload（payload 字段传 JSON object），也可只用 --选项")
    lines.append("详情：help <资源> 或 help <资源> <操作>；任意命令加 --help / -h 亦可")
    return "\n".join(lines)


HELP_SUMMARY = _build_help_summary()


#: `--remove-field` 可清除字段清单的**占位符**（#187 裁定③a）。
#:
#: 不让 help 文案手抄一份字段名：`taskService` 里曾残留一份手写副本遮蔽了派生常量，
#: 扩 `ClearField` 对运行时毫无影响，而变异自证照样「红」——红错了理由（#181 现场）。
#: 这里用占位符 + `_render_help` 运行时 `replace`，所以候选集合**永远**等于
#: `constants.ALLOWED_CLEAR_FIELDS` 当刻的值；`test_console_task` 里用 monkeypatch
#: 扩那个 frozenset，若文案不跟着变即红。
_CLEAR_FIELDS_PLACEHOLDER = "{ALLOWED_CLEAR_FIELDS}"


#: 手写补充说明（只有「参数清单之外」的话才该出现在这里）。
#:
#: 为什么仍然需要 overlay：schema 的 `description` 说明「这个参数是什么」，但说不出
#: 「这条命令整体的语义」——例如 `dept update` 是 PATCH、`dept rm` 会被
#: `DEPT_HAS_CHILDREN` 拒绝、同一个选项给两次只有最后一个生效。这些是命令级事实，
#: schema 里没有对应字段可派生。
#:
#: **只许放「补充」，不许放参数清单**：放进去就等于又抄了一份事实。
_HELP_OVERLAY: dict[str, tuple[str, ...]] = {
    # create 与 update 共用同一个原工具，所以**选项面完全相同**（选项面 = schema
    # property，不按命令裁剪）。代价是 create 的参数清单里会出现只对更新有意义的
    # 选项（--<资源>_id / --clear_fields / --overwrite）。这里不删它们 ——
    # 删了会让「schema 收得下、console 却说不知道」重新出现（那正是本单要消灭的
    # 口径分叉），只用一句话说清各自什么时候有意义。
    "dept create": (
        "新建必须给 --name / --responsibility / --member_names / --parent_name。",
        "--dept_id / --clear_fields / --overwrite 属更新语义，可在 create 时一并传入；"
        "命中已存在组织（含用 --dept_id 指定）时必须同时给 --overwrite true。",
    ),
    "agent create": (
        "新建必须给 --name / --role_template_name。",
        "--agent_id / --clear_fields / --overwrite 属更新语义，可在 create 时一并传入；"
        "命中已存在成员（含用 --agent_id 指定）时必须同时给 --overwrite true。",
    ),
    "room create": (
        "新建必须给 --name / --member_names（至少 2 人）。",
        "--room_id / --clear_fields / --overwrite 属更新语义，可在 create 时一并传入；"
        "命中已存在房间（含用 --room_id 指定）时必须同时给 --overwrite true。",
    ),
    "dept update": (
        "PATCH 语义：只改出现的字段；member_names 出现即全量替换。",
        "更新一个不存在的名字会落到原工具的「新建」分支（缺必填时报错，凑巧齐全会新建）。",
    ),
    "dept rm": (
        "有子部门且未加 --recursive 时由服务层以 DEPT_HAS_CHILDREN 拒绝，不会部分删除。",
        "加 --dry-run 只预演（跑完全部校验，不写库），返回体里是 would_delete。",
    ),
    "agent update": (
        "PATCH 语义：只改出现的字段；数组出现即全量替换。",
        "同名成员（离职名字被复用）会以 ambiguous_target 拒绝，请改用 id 定位。",
    ),
    "agent get": (
        "按名字查询走运行时列表：离职成员报 member_not_on_board，在职未加载报 agent_runtime_not_loaded。",
    ),
    "agent dm": ("与成员发起私聊；已有房间则直接返回。",),
    "room update": ("PATCH 语义：只改出现的字段。",),
    "room rm": ("DEPT 房间与运行中的房间不允许删除。",),
    "role update": (
        "PATCH 语义：只改出现的字段（type/soul/i18n 省略即保持现值）；目标模板必须已存在。",
    ),
    "role create": ("只能创建 USER 类型；SYSTEM 内置模板只读。",),
    "role rm": ("只能删除 USER 类型且当前未被任何成员使用的模板。",),
    "task ls": (
        "只列未完结任务（要含已完结请用 --status 显式列出，如 --status DONE,CANCELLED）。",
        "状态：TODO / PENDING / IN_PROGRESS / ON_HOLD / REVIEWING / DONE / CANCELLED",
    ),
    "task update": (
        "配置字段：new_status / note / reviewer_id。撤销验收人用 --remove-field reviewer_id。",
        "可清除的字段：" + _CLEAR_FIELDS_PLACEHOLDER + "（白名单外的字段会被拒绝并列出候选）。",
        "⚠️ 同一个 --选项 给两次**只有最后一个生效**：要传多个字段请用逗号写在一个里"
        "（--remove-field a,b），逗号值会去重保序。",
        "不传 note 不会清空已有 note（参数默认空串，不会覆盖）。",
        "既没有配置输入也没有 --remove-field 时报 no_update_action（不许静默成功）。",
    ),
    "reload": ("重载团队运行时；**会中断当前行动**。",),
}


#: 定位形态 → 用法行里的占位写法。`dept get root` 这类「数字当 id、其余当名字」的
#: 用法在既有用例里大量存在（D8），所以这里单列一张表，别在拼接处写 if。
_USAGE_TARGET_LABEL: dict[str, str] = {
    "both": "<id|名称>",
    "id": "<id>",
    "name": "<名称>",
}


def _usage_line(spec: cc.CommandSpec) -> str:
    """由命令表拼出用法行（参数清单不在这里，见 `_help_detail`）。"""
    parts = [spec.key] if spec.key else ["<资源> <操作>"]
    if spec.needs_target or spec.min_args:
        # 多个位置参数的命令（`container note <id> <text>`）逐个占位，占位名取 schema
        # 里对应位置的参数名 —— 否则用法行会把「2 个位置参数」写成 1 个，照着 help
        # 敲必然被 arity 拒。
        if not spec.needs_target and spec.min_args > 1:
            parts.extend(f"<{name}>" for name in spec.param_names[: spec.min_args])
        else:
            parts.append(_USAGE_TARGET_LABEL.get(spec.locator_kind, "<target>"))
    if spec.accepts_input:
        # 配置输入是可选的（也可以只用 --选项），用法行如实反映。
        parts.append("['<json>' | --from payload]")
    if spec.params:
        parts.append("[--选项 ...]")
    return " ".join(parts)


def _alias_reverse(spec: cc.CommandSpec) -> dict[str, tuple[str, ...]]:
    """「真参数名 → 别名元组」——schema 里只有真名，别名是命令行才有的。"""
    reverse: dict[str, tuple[str, ...]] = {}
    for alias, real in spec.alias_map.items():
        reverse[real] = reverse.get(real, ()) + (alias,)
    return reverse


@functools.lru_cache(maxsize=1)
def _help_detail() -> dict[str, str]:
    """全量详情表：一条命令一段，参数清单从 schema 派生。

    延迟到首次访问才生成（模块 `__getattr__` 把它暴露成 `HELP_DETAIL`）：生成过程
    要 import `tools` 拿 schema，而 `consoleTool` 在 `core.py` 里比 `tools` 更早被
    import —— 在 import 期就去拉 `tools` 会把包初始化顺序搅成循环。
    """
    detail: dict[str, str] = {}
    for spec in cc.COMMAND_SPECS:
        params = cs.describe_tool_params(spec.legacy_tool, aliases=_alias_reverse(spec))
        lines = [_usage_line(spec)]
        description = cs.tool_description(spec.legacy_tool)
        lines.append(f"用途：{description}" if description else "用途：（该工具未提供说明）")
        if params:
            lines.append("参数：")
            lines.extend(f"  {param}" for param in params)
        lines.extend(_HELP_OVERLAY.get(spec.key, ()))
        detail[spec.key] = "\n".join(lines)
    return detail


def __getattr__(name: str) -> Any:
    """把 `HELP_DETAIL` 做成「访问时才生成」的模块属性（PEP 562）。

    既保住 `from consoleTool import HELP_DETAIL` 的既有用法（`test_console_meta`
    就是这么用的），又不在 import 期拉起 `tools`。
    """
    if name == "HELP_DETAIL":
        return _help_detail()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _render_help(text: str) -> str:
    """占位符在**返回前**替换成当刻的 `ALLOWED_CLEAR_FIELDS`（#187 裁定③a）。

    候选字段集合因此永远跟着常量走，扩枚举后 help 自动更新，不会退化成快照。
    模块级 `ALLOWED_CLEAR_FIELDS` 这个名字必须保留：`test_console_task` 用
    monkeypatch 替换它来验证「候选集合真的源自该常量」。
    """
    return text.replace(
        _CLEAR_FIELDS_PLACEHOLDER,
        " / ".join(sorted(ALLOWED_CLEAR_FIELDS)),
    )


def _help_text(tokens: list[str]) -> dict[str, Any]:
    visible = _visible_command_specs()
    visible_keys = {spec.key for spec in visible}
    if len(tokens) <= 1:
        return {"success": True, "help": _render_help(_build_help_summary(visible))}
    resource = tokens[1].lower()
    action = tokens[2].lower() if len(tokens) > 2 else ""
    key = f"{resource} {action}".strip()
    if key in visible_keys:
        detail = _help_detail().get(key)
        if detail is not None:
            return {"success": True, "help": _render_help(detail)}
    available = [
        spec.action for spec in visible
        if spec.resource == resource and spec.action
    ]
    if available:
        return {"success": True, "help": f"{resource} 的可用操作：" + " | ".join(available)}
    raise ConsoleError(
        "unknown_command",
        f"未知命令：'{key}'。可用资源：{' | '.join(cc.RESOURCE_NAMES)}",
    )


# ---------------------------------------------------------------------------
# 选项值收敛
#
# 选项的**类型**从 schema 派生（`CommandSpec.option_kinds`）：integer 走正整数校验、
# array 走 csv 拆分、object 走 JSON 解析、boolean 在解析层就是不带值的开关、其余原样
# 收下。这里只做「形态转换」，**不校验取值合法性**（枚举 / 业务规则归服务层）：
# 在这里再判一遍就是第二个行为源，扩枚举时会漂移（#187 裁定③）。
# ---------------------------------------------------------------------------


def _require_positive_int(raw: Any, *, what: str, command: str) -> int:
    """把位置参数 / 选项值收敛成正整数，否则报 `invalid_target`。

    只做「是不是正整数」这一件事，**不查库**（#187 裁定⑨：不建 `_resolve_task_id`）。
    任务 id 是否存在由服务层判（`update_task` 返回「任务不存在：task_id=…」），
    在这里再查一次库会造出第二个「存在性判定」，并且多一次查询。

    为什么不能直接 `int(raw)`：`int()` 会接受 `" 187 "`、`"+187"` 这些「看起来像但
    不是纯数字」的形态，也会为 `"0x1"` 抛 `ValueError`、让 `int(True)` 得 1 —— 都不该
    被当成合法 id。这里先用 `str.strip()` 去空白再用 `str.isdigit()` 收口，
    `+` / `-` / `.` / 字母全被挡在外面。
    """
    text = str(raw).strip()
    # 全角数字（"１８７"）`isdigit()` 为 True 且 `int()` 能正确解析，放行；
    # 要挡的是符号 / 小数点 / 下划线 / 字母。
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
    """逗号分隔的多值选项 → 去重保序的字符串列表（#187 裁定④）。

    口径（help 里也写明，别只写在这里）：
    - 逗号分隔，**两侧空白被 trim**，空段被丢弃（`"a, ,b"` → `["a","b"]`）；
    - **去重保序**（`dict.fromkeys`），因为重复项对「清除字段」没有额外语义，
      却会让下游的「非法字段」报错把同一个名字列两遍；
    - 全空（`""` / `","` / 只有空白）⇒ 空列表，由调用方决定「空算不算错」。

    ⚠️ 已知限制（#187 裁定④，**只报不改**）：`_parse_options` 对同名选项是
    「后者覆盖前者」，所以 `--remove-field a --remove-field b` 只有 `b` 生效。
    逗号写法是不丢数据的唯一方式。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        parts = str(raw).split(",")
    return list(dict.fromkeys(p.strip() for p in parts if p.strip()))


def _convert_option(raw: Any, *, kind: str, name: str, command: str) -> Any:
    """按 schema 声明的类型把选项值转成工具要的形态。"""
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        return cp.parse_boolean_value(str(raw), name=name, command=command)
    if kind == "integer":
        return _require_positive_int(raw, what=f"--{name}", command=command)
    if kind == "array":
        return _split_csv(raw) or None
    if kind == "object":
        try:
            parsed = json.loads(str(raw))
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


def _is_blank(value: Any) -> bool:
    """「这个值算不算用户真的给了东西」——用于 `no_update_action` 的判定。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return not value
    return False


def _option_args(spec: cc.CommandSpec, parsed: ParsedCommand) -> dict[str, Any]:
    """已解析选项 → 转发参数（别名归一 + 按类型转换）。"""
    args: dict[str, Any] = {}
    for name, raw in parsed.options.items():
        real = spec.normalize_option(name)
        kind = spec.option_kinds.get(real) or spec.option_kinds.get(name) or "string"
        args[real] = _convert_option(raw, kind=kind, name=name, command=spec.key)
    return args


def _validate_input_keys(spec: cc.CommandSpec, data: dict[str, Any]) -> None:
    """配置输入的键必须是该工具真有的参数（`spec.param_names` 从 schema 派生）。

    旧实现按每条命令手抄要取哪些键，写错的键被静默丢弃 —— 模型以为配置生效了，
    实际没改，然后报告「已改好」。现在未知键直接报错并列出可用键。
    """
    allowed = spec.param_names
    if not allowed:
        # 转发目标缺失（命令表 `legacy_tool` 为空 ⇒ fail-closed）。这里**不做键名
        # 白名单**，原样转发，让注册表第 2 跳报「未知工具: xxx」。
        # 在这里拦成 `invalid_payload_field` 会把「命令表填错」伪装成「调用参数错」，
        # 掩盖真正的失败原因 —— 实测踩过：`dc_replace(legacy_tool="")` 的失败关闭
        # 判据原本该看到「未知工具」，结果看到「可用字段：」（空白名单）。
        return
    unknown = sorted(key for key in data if key not in allowed)
    if unknown:
        raise ConsoleError(
            "invalid_payload_field",
            f"`{spec.key}` 不认识这些配置字段：{' / '.join(unknown)}。"
            f"可用字段：{' / '.join(allowed)}",
            example=f"help {spec.key}",
        )


def _apply_input_defaults(spec: cc.CommandSpec, data: dict[str, Any]) -> dict[str, Any]:
    """按 `spec.input_defaults` 补「键不存在」的字段（显式 `null` 不覆盖）。

    必须在**冲突检测之后**应用：`dept create --member_names a,b '{...}'` 的 JSON 里
    没有 `member_names`，补默认值后再比较就会把「选项给了 member_names」误判成
    「两边都给了」——默认值不是调用方提供的输入来源。
    """
    data = dict(data)
    for name, value in spec.input_defaults:
        data.setdefault(name, value)
    return data


# ---------------------------------------------------------------------------
# 定位（D8：数字当 id、其余当名字）
# ---------------------------------------------------------------------------


def _locator_args(spec: cc.CommandSpec, target: str, *, require_positive_id: bool) -> dict[str, Any]:
    """位置参数 → 定位参数。

    三种形态（见 `consoleCommands.Locator`）：
    - **both**：数字当 id、其余当名字（D8 兼容方案）；
    - **id**：必须是正整数（如 `task get 187`），否则 `invalid_target`；
    - **name**：原样作为名字。

    `require_positive_id` 只对 `both` 的数字分支起作用：`task get 0` 必须当场报
    `invalid_target`（0 号任务不存在，先报错比多查一次库清楚）。
    """
    locator = spec.locator
    if locator is None:
        return {}
    if locator.kind == "id":
        return {locator.id_param: _require_positive_int(target, what="id", command=spec.key)}
    if locator.kind == "name":
        return {locator.name_param: (target or "").strip()}
    text = (target or "").strip()
    if require_positive_id and text.isdigit():
        return {locator.id_param: _require_positive_int(text, what="id", command=spec.key)}
    if text.isdigit():
        return {locator.id_param: int(text)}
    return {locator.name_param: text}


def _forward(
    parsed: ParsedCommand,
    /,
    *,
    post_process: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    **args: Any,
) -> NoReturn:
    """把本次调用转交给 `parsed.spec.legacy_tool`（#160 唯一出口）。

    目标名**只从命令表取**，不在处理里重复写一遍字面量：否则「声明的等价工具」与
    「实际转发的工具」会各说各话，而 `test_every_legacy_tool_has_a_category` 这类
    静态护栏就再也咬不住漂移。漏填 `legacy_tool` 的后果也随之明确——转发目标为空，
    第 2 跳在注册表里查不到，按「未知工具」拒绝（fail-closed，而不是静默放行）。

    参数组装保持在调用点（与原 `legacy.X(...)` 的字面形式一一对应），所以路由的
    语义改动完全可从 diff 看出来。`_context` 不放进来：它由 `run_tool_call` 按当前
    上下文注入。
    """
    raise ForwardToolCall(parsed.spec.legacy_tool, args, post_process=post_process)


# ---------------------------------------------------------------------------
# 5 个动词处理器（#196 P2）
#
# 每个处理器的输入都是「命令表 + 已解析命令」，不认具体的 resource/action —— 加一条
# `resource action` 只需要在命令表里填一行，处理器一行都不用改。
# ---------------------------------------------------------------------------


def _handle_list(parsed: ParsedCommand, _payload: dict[str, Any] | None = None) -> NoReturn:
    """LIST：只转发选项（`dept ls` / `task ls --status TODO`）。"""
    _forward(parsed, **_option_args(parsed.spec, parsed))


def _handle_get(parsed: ParsedCommand, _payload: dict[str, Any] | None = None) -> NoReturn:
    """GET：定位 + 转发，不改库。"""
    spec = parsed.spec
    args = _option_args(spec, parsed)
    args.update(_locator_args(spec, parsed.target or "", require_positive_id=True))
    _forward(parsed, **args)


def _handle_delete(parsed: ParsedCommand, _payload: dict[str, Any] | None = None) -> NoReturn:
    """DELETE：定位 + 转发。

    影响范围（旧实现的 `impact`）不再由 console 拼：原工具返回体里已有 `deleted` /
    `would_delete` 清单（#196 P1 加的），再叠一份 `impact` 等于同一件事说两遍，
    而且失败时还得记得别叠（旧实现漏过这个）。
    """
    spec = parsed.spec
    args = _option_args(spec, parsed)
    args.update(_locator_args(spec, parsed.target or "", require_positive_id=False))
    _forward(parsed, **args)


#: 不属于「更新动作」的机制参数：只影响定位或覆盖保护，不产生字段变更。
#: `dept update root --overwrite` 若被当成「有动作」，原工具会按 PATCH
#: 语义「无字段可改」仍返回 success=True —— 正是 #187 禁止的静默成功。
_MECHANISM_OPTIONS = frozenset({"overwrite"})


def _effective_action_names(spec: cc.CommandSpec, values: dict[str, Any]) -> set[str]:
    """`values` 里真正构成一次更新动作的键（排除定位与覆盖保护等机制参数）。"""
    mechanism = set(_MECHANISM_OPTIONS)
    locator = spec.locator
    if locator is not None:
        mechanism.update(name for name in (locator.id_param, locator.name_param) if name)
    return {
        name
        for name, value in values.items()
        if name not in mechanism and not _is_blank(value)
    }


def _handle_save(parsed: ParsedCommand, payload: dict[str, Any] | None = None) -> NoReturn:
    """SAVE：配置输入 + 选项 + 定位。

    PATCH 语义由原工具负责（#196 P1/P2）：省略的字段就是没传，原工具自己取现值。

    两种输入来源（`--选项` / 配置 JSON）的契约（#198）：
    - 同名字段**禁止静默覆盖**：别名归一后仍与 JSON 键重名 ⇒ `ambiguous_input_source`；
    - 目标定位参数（如 `--dept_id`）与位置参数 `<target>` 同时给也判冲突 ——
      否则 `dept update root --dept_id 5` 会把 5 号部门改名成 root；
    - `create` 既无有效选项、也无配置输入 ⇒ `missing_input_source`；
    - `update` 没有有效动作（机制参数不算）⇒ `no_update_action`。
    """
    spec = parsed.spec
    option_args = _option_args(spec, parsed)
    raw_input = dict(cp._input_payload_for(parsed, payload))
    _validate_input_keys(spec, raw_input)

    duplicates = sorted(set(option_args) & set(raw_input))
    if duplicates:
        raise ConsoleError(
            "ambiguous_input_source",
            f"这些参数同时出现在 --选项 与配置输入中：{' / '.join(duplicates)}。请二选一。",
            example=f"help {spec.key}",
        )

    locator = spec.locator
    locator_keys = (
        {name for name in (locator.id_param, locator.name_param) if name}
        if locator is not None
        else set()
    )
    overlap = sorted(locator_keys & (set(option_args) | set(raw_input)))
    if overlap:
        raise ConsoleError(
            "ambiguous_input_source",
            f"目标定位参数 {' / '.join(overlap)} 不能与位置参数 <target> 重复提供；"
            "请二选一。",
            example=f"help {spec.key}",
        )

    # 冲突检测必须在默认值之前：默认值不是调用方给的输入来源（见 `_apply_input_defaults`）。
    config = _apply_input_defaults(spec, raw_input)
    config.update(option_args)

    effective = _effective_action_names(spec, config)
    if spec.needs_target:
        # 「什么都没有」必须显式报错，**不许静默成功**（#187 裁定①的附加硬要求）：
        # 一个什么都没做的 update 返回 success=True，模型会当成已改好继续往下走，
        # 比直接报错贵得多。判定看**有没有真的产生变更**，不看参数个数：
        # `note=''` / `note='   '` 这类空值不写任何事件（服务层要求非空 note），
        # 所以按「全部为空」判。
        if not effective:
            raise ConsoleError(
                "no_update_action",
                f"`{spec.key} {parsed.target}` 没有可执行的动作：请至少给出一个配置字段，"
                "或用 --remove-field 指定要清除的字段。",
                example=f"help {spec.key}",
            )
    elif not raw_input and not effective:
        # create 允许「只给 --选项」，但两者全无时连工具都调不起来：
        # 保留原有的 `missing_input_source` 错误码（既有用例对外契约）。
        raise ConsoleError(
            "missing_input_source",
            "create 需要配置输入：内联 JSON、--from payload，或至少一个 --选项。",
            example=f"{spec.key} ...",
        )

    args = dict(config)
    args.update(_locator_args(spec, parsed.target or "", require_positive_id=False))

    if spec.patch_by_name and args.get("overwrite") is None:
        # `update` 的语义就是更新：原工具要求更新已有记录必须显式 overwrite=true
        # （name / id 两种定位都一样）。不替调用方表达更新意图，`dept update root`
        # 就改不了自己的目标 —— 详见 `CommandSpec.patch_by_name`。
        args["overwrite"] = True

    _forward(parsed, **args)


def _handle_direct(parsed: ParsedCommand, _payload: dict[str, Any] | None = None) -> NoReturn:
    """DIRECT：位置参数**按 schema 声明顺序**逐个映射成工具参数。

    也就是 `positional[i] → param_names[i]`（`container note <id> <text>` 两个位置
    参数分别落到 `container_id` / `note`）。参数名不手抄，取的是 schema 的声明顺序：
    工具改了参数名、或调整了前两个参数的顺序，命令行自动跟随。

    只做这一件映射：选项（`--x`）仍由 `_option_args` 处理，位置参数个数仍由
    `_check_arity` 按 `min_args` / `max_args` 判定，verb 分发不变。存量命令全部
    `max_args <= 1`，只走 `i == 0` 这一路，行为与「只映射第一个」完全一致。
    """
    spec = parsed.spec
    args = _option_args(spec, parsed)
    if parsed.positional:
        names = spec.param_names
        if not names:
            raise ConsoleError(
                "unknown_command",
                f"`{spec.key}` 的转发目标 {spec.legacy_tool!r} 没有可用的参数定义，"
                "属实现缺陷（命令表的 legacy_tool 填错？）。",
            )
        if len(parsed.positional) > len(names):
            # arity 已按 max_args 拦过一次；能走到这里说明命令表的 max_args 比
            # schema 参数还多 —— 那是命令表写错了，不是用户输入错。
            raise ConsoleError(
                "invalid_argument_count",
                f"`{spec.key}` 的位置参数比 {spec.legacy_tool} 的参数还多"
                f"（{len(parsed.positional)} > {len(names)}），属实现缺陷。",
            )
        for index, value in enumerate(parsed.positional):
            args[names[index]] = value
    _forward(parsed, **args)


#: verb → 处理器。声明成 `Mapping`（读多态）而不是 `dict`：`dict` 的值类型不变，
#: 而各处理器的第二参数一个叫 `payload`、其余叫 `_payload`，带形参名的 callable
#: 与不带名的 `Callable[[A, B], R]` 只能单向兼容，双向检查就会失败（#205 修的
#: mypy error）。这里只 `.get()` 取用、从不改写，`Mapping` 已足够表达所需能力，
#: 运行时仍是同一个 dict。
_VERB_HANDLERS: Mapping[str, Callable[[ParsedCommand, dict[str, Any] | None], NoReturn]] = {
    "LIST": _handle_list,
    "GET": _handle_get,
    "SAVE": _handle_save,
    "DELETE": _handle_delete,
    "DIRECT": _handle_direct,
}


def _dispatch(parsed: ParsedCommand, payload: dict[str, Any] | None) -> NoReturn:
    """按命令表的 `verb` 分发。加命令不必动这里，除非引入了新的**动作语义**。"""
    spec = parsed.spec
    handler = _VERB_HANDLERS.get(spec.verb)
    if handler is None:
        raise ConsoleError(
            "unknown_command",
            f"`{spec.key}` 的动词 {spec.verb!r} 没有对应处理器，属实现缺陷。",
        )
    handler(parsed, payload)
    raise ConsoleError(  # pragma: no cover —— 上面每个处理器都是 NoReturn
        "unknown_command",
        f"`{spec.key}` 的处理器没有转发也没有报错，属实现缺陷。",
    )


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

async def console(command: str, payload: Optional[dict] = None, _context: ToolCallContext = None) -> dict:
    """团队/组织/成员/房间/角色模板的管理控制台，用命令行完成管理操作。

    Args:
        command: 要执行的命令。资源名：dept(部门) / agent(成员) / room(房间) / role(角色模板) / task(协作任务) / container(容器)。示例：'dept ls'、'agent create --from payload'、'room get 研发部'、'task ls --status TODO'、'task update 187 --remove-field reviewer_id'、'container note a1b2c3d4e5f6 "跑前端构建"'。输入 'help' 查看全部命令；任意命令加 --help / -h 可看该命令的参数清单。
        payload: 仅当 command 使用 --from payload 时提供，作为 create/update 的 JSON 配置对象；其他命令不得提供。
    """
    try:
        tokens = cp._tokenize(command)
    except ConsoleError as exc:
        return cp._error_payload(exc)

    # 全局 `--help` / `-h`（D12）：任何位置命中即短路返回该命令的 help，不占选项白名单。
    if any(token in cp.HELP_FLAGS for token in tokens):
        help_tokens = [token for token in tokens if token not in cp.HELP_FLAGS]
        try:
            return _help_text(["help"] + help_tokens[:2])
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
        parsed = _parse_command_tokens(spec, tokens, payload)
        return _dispatch(parsed, payload)
    except ConsoleError as exc:
        return cp._error_payload(exc)
    except ForwardToolCall:
        # 转发信号必须原样上抛（#160）：它是控制流，不是业务失败。
        # 这个 `except` 必须排在下面的 `except Exception` **之前**——后者会把任何
        # 异常转成失败返回体，转发信号一旦在这里被吞，统一入口
        # `execute_tool_call` 就再也看不到它，整个设计静默失效。
        raise
    except Exception as exc:  # 业务异常透传（draft §6）
        # #189：直接走唯一实现 `toolResult.error_result`，不再绕 `tools._tool_error_result`
        # —— 后者本身也只是它的别名，绕一层会让「第二份转换」有第二个落脚点。
        return error_result(exc)


def _parse_command_tokens(
    spec: cc.CommandSpec,
    tokens: list[str],
    payload: dict[str, Any] | None = None,
) -> ParsedCommand:
    """把 token 列表解析成 `ParsedCommand`，校验参数个数与 `payload`（**唯一**解析入口）。

    为什么要抽出来（#165 实测教训）：`console()` 主入口和
    `console_triggers_self_interrupt` 都要做「切选项 + 校验参数个数」，一开始两处各
    抄一遍 —— 结果谓词只解析了命令名、没解析选项，于是 `reload --confirm xxx`（选项
    非法、真执行会在 `_parse_options` 失败）被判定成「会中断」，留下**假的中断标记**。
    两处口径各写一遍就一定会漂移，抽成一个函数后物理上不可能不一致。

    `payload` 的合法性也收在这里（#166-A 同类：谓词漏校验会写假标记）：不接受配置
    输入的命令传了 payload，真执行会在进入 handler 前报 `ambiguous_input_source`，
    因而绝不会中断；判定器必须看到同一条结论。静默忽略 payload 的后果很隐蔽：模型
    以为配置生效了，实际什么都没改，它会接着报告「已改好」——比直接报错贵得多。
    """
    if not cc.command_console_visible(spec):
        raise ConsoleError(
            "unknown_command",
            f"命令当前不可用：`{spec.key}`（对应工具已被环境开关停用）。",
        )
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
    if payload is not None and not spec.accepts_input:
        raise ConsoleError(
            "ambiguous_input_source",
            f"`{spec.key}` 不接受配置输入，请去掉 payload 字段。",
            example=spec.key,
        )
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
    前置条件——**只要这条命令能成功执行，它就一定会中断**。

    **必须走完整的解析链路，不能只看命令名**：`reload --confirm xxx` 现在会在
    `_parse_options` 撞 `unknown_option` 直接失败、根本到不了转发，如果这里只按
    「命令名是 reload」返回 True，历史条目就会带上一个**假的中断标记**——下次该条目
    因别的原因停在 INIT 时，会被 `_advance_step` 当成「已执行过」自动判成功。
    预警一句：这类"谓词说会中断、实际没执行"的错位比漏判更难查，所以这里复刻
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
        # `--help` / `-h` 在 `console()` 里是短路返回，永远不会走到转发 ⇒ 不会中断。
        if any(token in cp.HELP_FLAGS for token in tokens):
            return False
        spec, _err = cc.resolve_command_spec(tokens)
        if spec is None or not spec.self_interrupt:
            return False
        payload = args.get("payload")
        if payload is not None and not isinstance(payload, dict):
            # `payload` 的类型校验发生在 handler 之前（json schema 校验），非法值
            # 根本到不了 `console()` ⇒ 不可能发生自中断。判定器与真执行必须同结论。
            return False
        # 走与 console() **完全相同**的解析链路：选项非法 / 参数个数不对 / 不接受
        # 输入却带了 payload 的命令都会在执行前失败，因此不能中断（否则留下假标记）。
        _parse_command_tokens(spec, tokens, payload)
        return True
    except Exception:  # noqa: BLE001 —— 判定器绝不把异常抛进 turn runner
        return False
