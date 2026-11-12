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

**PATCH 语义现在在原工具里**（#196 P1 已下沉到 `save_dept` / `save_agent` /
`save_room`）：省略字段 = 保持现值。所以 console 侧不再读库拼补丁，直接把
「出现了哪些键」原样转过去。唯一的例外是 `role update` —— `save_role_template`
没有 PATCH 语义（name/type/soul 三者必填），必须先读出现值补齐
（命令表里 `needs_padding=True`）。

## help 也是派生的

`HELP_SUMMARY` / `HELP_DETAIL` 由命令表 + `consoleSchema` 生成，手写的只剩
「参数清单之外的一句话补充」（`_HELP_OVERLAY`：PATCH 语义提示、`--remove-field`
候选占位符、重复选项只最后一个生效……）。加一条新命令只改命令表一处：help 行、
参数清单、选项白名单、布尔开关判定全部自动跟随。
"""

from __future__ import annotations

import functools
import json
from typing import Any, Callable, NoReturn, Optional

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


def _build_help_summary() -> str:
    """一行一个资源，操作按命令表顺序拼接（摘要里**不出现**参数清单）。"""
    lines: list[str] = []
    for resource, specs in cc.SPECS_BY_RESOURCE.items():
        actions = "|".join(spec.action for spec in specs if spec.action)
        lines.append(f"{resource:<8}{actions}".rstrip())
    lines.append("用法：<资源> <操作> [位置参数] [--选项 ...]")
    lines.append("create/update 的配置输入：'<json>' 或 --from payload（payload 字段传 JSON object）")
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
    # 选项（--<资源>_id / --clear_fields / --overwrite_existing）。这里不删它们 ——
    # 删了会让「schema 收得下、console 却说不知道」重新出现（那正是本单要消灭的
    # 口径分叉），只用一句话说清各自什么时候有意义。
    "dept create": (
        "新建必须给 --name / --responsibility / --member_names / --parent_name。",
        "--dept_id / --clear_fields / --overwrite_existing 属更新语义，可在 create 时一并传入，"
        "由原工具自己判定（如 --dept_id 命中已存在组织即转为更新）。",
    ),
    "agent create": (
        "新建必须给 --name / --role_template_name。",
        "--agent_id / --clear_fields / --overwrite_existing 属更新语义，可在 create 时一并传入，"
        "由原工具自己判定。",
    ),
    "room create": (
        "新建必须给 --name / --member_names（至少 2 人）。",
        "--room_id / --clear_fields / --overwrite_existing 属更新语义，可在 create 时一并传入，"
        "由原工具自己判定。",
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
        "PATCH 语义（由 console 补齐）：save_role_template 的 name/type/soul 三必填且没有"
        "「省略即保持」的语义，所以这条命令会先读出现值再合并；目标模板必须已存在。",
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
        parts.append(_USAGE_TARGET_LABEL.get(spec.locator_kind, "<target>"))
    if spec.accepts_input:
        parts.append("'<json>' | --from payload")
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
    if len(tokens) <= 1:
        return {"success": True, "help": _render_help(HELP_SUMMARY)}
    resource = tokens[1].lower()
    action = tokens[2].lower() if len(tokens) > 2 else ""
    key = f"{resource} {action}".strip()
    detail = _help_detail().get(key)
    if detail is not None:
        return {"success": True, "help": _render_help(detail)}
    available = [spec.action for spec in cc.SPECS_BY_RESOURCE.get(resource, ()) if spec.action]
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
        return True
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


def _input_args(spec: cc.CommandSpec, parsed: ParsedCommand, payload: dict[str, Any] | None) -> dict[str, Any]:
    """配置输入（内联 JSON / `--from payload`）→ 转发参数。

    **键名必须是该工具真有的参数**（`spec.param_names` 从 schema 派生）：旧实现按
    每条命令手抄要取哪些键，写错的键被静默丢弃 —— 模型以为配置生效了，实际没改，
    然后报告「已改好」。现在未知键直接报错并列出可用键。

    缺省的字段按 `spec.input_defaults` 补值（`dept create` 的 `member_names: []`、
    `role create` 的 `type: "USER"`）—— 那是**命令层契约**，不是工具签名的默认值。
    """
    data = dict(cp._input_payload_for(parsed, payload))
    if not data and not spec.input_defaults:
        return {}
    allowed = spec.param_names
    if not allowed:
        # 转发目标缺失（命令表 `legacy_tool` 为空 ⇒ fail-closed）。这里**不做键名
        # 白名单**，原样转发，让注册表第 2 跳报「未知工具: xxx」。
        # 在这里拦成 `invalid_payload_field` 会把「命令表填错」伪装成「调用参数错」，
        # 掩盖真正的失败原因 —— 实测踩过：`dc_replace(legacy_tool="")` 的失败关闭
        # 判据原本该看到「未知工具」，结果看到「可用字段：」（空白名单）。
        return data
    unknown = sorted(key for key in data if key not in allowed)
    if unknown:
        raise ConsoleError(
            "invalid_payload_field",
            f"`{spec.key}` 不认识这些配置字段：{' / '.join(unknown)}。"
            f"可用字段：{' / '.join(allowed)}",
            example=f"help {spec.key}",
        )
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


def _handle_list(parsed: ParsedCommand) -> NoReturn:
    """LIST：只转发选项（`dept ls` / `task ls --status TODO`）。"""
    _forward(parsed, **_option_args(parsed.spec, parsed))


def _handle_get(parsed: ParsedCommand) -> NoReturn:
    """GET：定位 + 转发，不改库。"""
    spec = parsed.spec
    args = _option_args(spec, parsed)
    args.update(_locator_args(spec, parsed.target or "", require_positive_id=True))
    _forward(parsed, **args)


def _handle_delete(parsed: ParsedCommand) -> NoReturn:
    """DELETE：定位 + 转发。

    影响范围（旧实现的 `impact`）不再由 console 拼：原工具返回体里已有 `deleted` /
    `would_delete` 清单（#196 P1 加的），再叠一份 `impact` 等于同一件事说两遍，
    而且失败时还得记得别叠（旧实现漏过这个）。
    """
    spec = parsed.spec
    args = _option_args(spec, parsed)
    args.update(_locator_args(spec, parsed.target or "", require_positive_id=False))
    _forward(parsed, **args)


async def _handle_save(
    parsed: ParsedCommand,
    payload: dict[str, Any] | None,
    context: ToolCallContext | None,
) -> NoReturn:
    """SAVE：配置输入 + 选项 + 定位。

    PATCH 语义由原工具负责（#196 P1）：省略的字段就是没传，原工具自己取现值。
    例外是 `needs_padding=True` 的 `role update`（原工具没有 PATCH 语义）。
    """
    spec = parsed.spec
    config = _option_args(spec, parsed)
    config.update(_input_args(spec, parsed, payload))

    if spec.input_optional and all(_is_blank(value) for value in config.values()):
        # 「什么都没有」必须显式报错，**不许静默成功**（#187 裁定①的附加硬要求）：
        # 一个什么都没做的 update 返回 success=True，模型会当成已改好继续往下走，
        # 比直接报错贵得多。判定看**有没有真的产生变更**，不看参数个数：
        # `note=''` / `note='   '` 这类空值不写任何事件（服务层要求非空 note），
        # 所以按「全部为空」判。
        #
        # ⚠️ 必须只看**配置部分**（`config`），不能看合并了定位参数之后的 `args`：
        # `task_id` 永远在，`args` 恒非空 ⇒ 判据恒假、静默成功（本单实测踩过）。
        raise ConsoleError(
            "no_update_action",
            f"`{spec.key} {parsed.target}` 没有可执行的动作：请至少给出一个配置字段，"
            "或用 --remove-field 指定要清除的字段。",
            example=f"help {spec.key}",
        )

    args = dict(config)
    args.update(await _save_locator_args(spec, parsed.target or "", context))

    if spec.patch_by_name and args.get("overwrite_existing") is None:
        # `update` 的语义就是更新：按名字定位时原工具会把「同名已存在」判成冲突
        # （`overwrite_existing` 默认 False，那是给 create 用的保护）。不表达更新意图
        # 就改不了自己的目标 —— 详见 `CommandSpec.patch_by_name`。
        # 调用方显式给了 `--overwrite_existing` 就尊重它（包括显式 false）。
        args["overwrite_existing"] = True

    if spec.needs_padding:
        await _pad_missing_required(spec, args)

    _forward(parsed, **args)


async def _save_locator_args(
    spec: cc.CommandSpec, target: str, context: ToolCallContext | None
) -> dict[str, Any]:
    """update 的定位参数。

    `ambiguous_by_name`（只有 `agent update`）要先按名字查一次：`save_agent` 在工具层
    按名字取运行时首个，同名两人时会**静默改错人**。查到了就用 id 定位，顺带避开
    「按名字 PATCH 却又把名字撞成已存在」的歧义。查不到就把名字原样传下去，让工具层
    自己报「未找到 / 新建缺必填」——这里不抄一遍它的文案，否则同一句错误有两个来源。
    """
    locator = spec.locator
    if locator is None or locator.kind == "id":
        return _locator_args(spec, target, require_positive_id=True)
    args = _locator_args(spec, target, require_positive_id=False)
    if locator.id_param is None or not locator.ambiguous_by_name:
        return args
    team_id = getattr(context, "team_id", None)
    if team_id is None:
        return args
    row = await cp._resolve_agent_by_target(team_id, target)
    if row is not None:
        return {locator.id_param: row.id}
    return args


async def _pad_missing_required(spec: cc.CommandSpec, args: dict[str, Any]) -> None:
    """给「必填但没有省略即保持语义」的工具补上现值（目前只有 `role update`）。

    `save_role_template(name, type, soul, ...)` 三个参数都必填、且没有 PATCH 语义：
    `role update tester '{"soul":"s2"}'` 若不补齐 name/type，工具会直接报缺参。
    所以先按名字（或新名字）读出现值填进去 —— 这是 console 侧唯一保留的「读库补丁」，
    因为它补的是**工具契约缺的那一块**，不是业务逻辑。
    """
    from dal.db import gtRoleTemplateManager

    target = args.get("name")
    if not isinstance(target, str) or not target.strip():
        return
    existing = await gtRoleTemplateManager.get_role_template_by_name(target.strip())
    if existing is None:
        # 没有现值可补：把缺的必填项问题留给工具层报（它的文案是权威）。
        return
    args.setdefault("type", existing.type.name)
    args.setdefault("soul", existing.soul)
    args.setdefault("i18n", existing.i18n or None)
    args["overwrite_existing"] = True


def _handle_direct(parsed: ParsedCommand) -> NoReturn:
    """DIRECT：位置参数就是工具参数（`agent wake bob` / `agent dm bob` / `reload`）。"""
    spec = parsed.spec
    args = _option_args(spec, parsed)
    if parsed.positional:
        # 唯一的位置参数名由 schema 的第一个参数给出：`wake_up_agent(agent_name)` /
        # `start_chat(agent_name)`。写在这里而不是手抄 `agent_name`，是为了让「工具
        # 改了参数名」自动跟随。
        names = spec.param_names
        if not names:
            raise ConsoleError(
                "unknown_command",
                f"`{spec.key}` 的转发目标 {spec.legacy_tool!r} 没有可用的参数定义，"
                "属实现缺陷（命令表的 legacy_tool 填错？）。",
            )
        args[names[0]] = parsed.target or ""
    _forward(parsed, **args)


_VERB_HANDLERS: dict[str, str] = {
    "LIST": "_handle_list",
    "GET": "_handle_get",
    "DELETE": "_handle_delete",
    "DIRECT": "_handle_direct",
}


async def _dispatch(
    parsed: ParsedCommand,
    payload: dict[str, Any] | None,
    context: ToolCallContext | None,
) -> dict[str, Any]:
    """按命令表的 `verb` 分发。加命令不必动这里，除非引入了新的**动作语义**。"""
    spec = parsed.spec
    if spec.verb == "SAVE":
        await _handle_save(parsed, payload, context)
    handler_name = _VERB_HANDLERS.get(spec.verb)
    if handler_name is None:
        raise ConsoleError(
            "unknown_command",
            f"`{spec.key}` 的动词 {spec.verb!r} 没有对应处理器，属实现缺陷。",
        )
    globals()[handler_name](parsed)
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
        command: 要执行的命令。资源名：dept(部门) / agent(成员) / room(房间) / role(角色模板) / task(协作任务)。示例：'dept ls'、'agent create --from payload'、'room get 研发部'、'task ls --status TODO'、'task update 187 --remove-field reviewer_id'。输入 'help' 查看全部命令；任意命令加 --help / -h 可看该命令的参数清单。
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
        return await _dispatch(parsed, payload, _context)
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
        # 走与 console() **完全相同**的解析链路：选项非法 / 参数个数不对的命令会在
        # 执行前就失败，因此不能中断（否则留下假标记）。
        _parse_command_tokens(spec, tokens)
        return True
    except Exception:  # noqa: BLE001 —— 判定器绝不把异常抛进 turn runner
        return False
