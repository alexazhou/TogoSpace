"""把 legacy tool 的 JSON Schema 翻译成命令行的样子：选项名、类型、是否必填，供 help 与解析共用。

现象：手写一份 help 或选项白名单，等于把同一件事记了两遍；新增参数或改了名字，副本不会报错，任何用例也不会变红。
原因：`tools.py` 的函数签名和 docstring 才是模型看到的参数说明，`consoleTool.HELP_DETAIL` 这类手抄副本与它之间没有任何自动检查（`taskService` 的手抄 `clear_fields` 就出过同类问题）。
要改这里：加参数只改 `tools.py`，别手抄进 `HELP_DETAIL` / `CommandSpec.options`，也别在本层校验值是否合法（那归 `#189`）；改完跑 `tests/unit/service/funcToolService/test_console_kebab_option.py`，背景见 `docs/tech/11_refactor/console_tool_review.md` 第五节。"""

from __future__ import annotations

import functools
from typing import Any, Optional

#: 执行上下文注入参数——不是模型可见参数，一律不派生。
_SKIPPED_PARAMS = ("_context",)

#: 参数说明截断长度（help 里每个参数一行，太长会把 help 撑回「全量 schema」的体积）。
_DESCRIPTION_MAX = 48


@functools.lru_cache(maxsize=None)
def _tool_metadata(tool_name: str) -> Optional[dict]:
    """某工具的完整函数元数据（`get_function_metadata` 结果）；取不到返回 `None`。

    缓存放在这一层而不是分别放在 `tool_parameters` / `tool_description` 里：两者
    本来会各自 `get_function_metadata` 一次，`help <资源> <操作>` 展一条命令就多
    跑一遍签名反射与 docstring 解析。元数据只随代码变化，按工具名缓存是安全的。

    与 `/tool_args_validation.get_tool_parameters_schema` 的区别：那边是**校验用**的
    （带可调用身份缓存、带变长参数摘除），这里只读不校验，且必须能在 import 期
    （模块加载生成 help）调用。取不到就返回 None —— help 退化成一个占位说明，
    而不是让整个 `consoleTool` 模块 import 失败。
    """
    from . import tools as legacy_tools
    from .funcToolType import get_function_metadata

    func = getattr(legacy_tools, tool_name, None)
    if func is None:
        return None
    try:
        metadata = get_function_metadata(tool_name, func)
    except Exception:  # noqa: BLE001 —— 派生失败只降级，不能把 import 弄崩
        return None
    return metadata if isinstance(metadata, dict) else None


@functools.lru_cache(maxsize=None)
def tool_parameters(tool_name: str) -> Optional[dict]:
    """取某工具的 `parameters` 子 schema；取不到返回 `None`（调用方降级，不抛异常）。"""
    metadata = _tool_metadata(tool_name)
    params = metadata.get("parameters") if metadata else None
    return params if isinstance(params, dict) else None


def param_schemas(tool_name: str) -> dict[str, dict]:
    """参数名 → 该参数的 schema 片段（按签名声明顺序，已剔除 `_context`）。"""
    params = tool_parameters(tool_name) or {}
    properties = params.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        name: prop
        for name, prop in properties.items()
        if name not in _SKIPPED_PARAMS and isinstance(prop, dict)
    }


def required_params(tool_name: str) -> tuple[str, ...]:
    """必填参数名（按 schema 声明顺序，已剔除 `_context`）。"""
    params = tool_parameters(tool_name) or {}
    required = params.get("required")
    if not isinstance(required, list):
        return ()
    return tuple(name for name in required if name not in _SKIPPED_PARAMS)


def param_kind(prop: dict) -> str:
    """schema 片段 → 命令行的类型类别。

    `["string", "null"]` 取非 null 的那个；`anyOf`（如 `list_tasks.scope` 的
    `enum|null`）取任何一个分支的非 null `type`。取不到就当 `string` —— 命令行
    按字符串收下、由第 2 跳校验，比在这里瞎猜成 integer 更安全。
    """
    declared = prop.get("type")
    if isinstance(declared, list):
        for item in declared:
            if item != "null":
                return str(item)
        return "string"
    if isinstance(declared, str):
        return declared
    for branch in prop.get("anyOf") or ():
        if isinstance(branch, dict):
            kind = param_kind(branch) if "type" in branch else ""
            if kind:
                return kind
    return "string"


def param_kinds(tool_name: str) -> dict[str, str]:
    """参数名 → 类型类别（`integer` / `boolean` / `array` / `object` / `string`）。

    P2 的选项值收敛按这份映射做：integer 走正整数校验、array 走 csv 拆分、
    object 走 JSON 解析、boolean 在解析层就是不带值的开关、其余原样收下。
    与 `boolean_params` 同源（都从 `param_schemas` + `param_kind` 派生），
    不另存一份类型表。
    """
    return {name: param_kind(prop) for name, prop in param_schemas(tool_name).items()}


def tool_description(tool_name: str) -> str:
    """工具 docstring 的首句 —— 命令的「一行用途」。

    help 的用途行也从 schema 派生：手抄一份「dept ls 列出部门树」与真正的
    工具 docstring 之间没有任何机械约束，改了工具说明 help 会静默过期。
    取不到就返回空串，由调用方决定怎么降级。
    """
    if not tool_parameters(tool_name):
        return ""
    metadata = _tool_metadata(tool_name)
    return _first_clause(metadata.get("description") if metadata else "")


def boolean_params(tool_name: str) -> frozenset[str]:
    """该工具里 schema 声明为 boolean 的参数名 —— 命令行上是不带值的开关。

    `_BOOLEAN_OPTIONS` 从这份派生集合取值（旧实现是手写 `frozenset({"recursive"})`，
    `dry_run` 加进 schema 后它不会自动变成开关，会被当成「需要值的选项」而在
    `--dry-run` 后面把下一个 token 吃掉）。
    """
    return frozenset(
        name for name, prop in param_schemas(tool_name).items() if param_kind(prop) == "boolean"
    )


def _first_clause(description: Any) -> str:
    """取说明文字的第一句（help 里一行一个参数，不能把整段 docstring 搬进来）。"""
    if not isinstance(description, str):
        return ""
    text = description.strip().replace("\n", " ")
    for sep in ("；", "。", "，", ";"):
        cut = text.find(sep)
        if cut > 0:
            text = text[:cut]
            break
    text = text.strip()
    if len(text) > _DESCRIPTION_MAX:
        text = text[:_DESCRIPTION_MAX].rstrip() + "…"
    return text


def _kebab(name: str) -> str:
    """参数真名（下划线式）→ help 展示形态（kebab 式，#221 需求 3）。

    与 `consoleCommands.to_kebab` 同规则（两边各一份是因为包内 import 方向：
    consoleCommands → consoleSchema 不能反过来模块级依赖，否则成环）。结构判据
    （test_console_kebab_option.py）会把两份实现对着钉死：同输入必须同输出。
    """
    kebab = name.replace("_", "-")
    if "--" in kebab or kebab.startswith("-") or kebab.endswith("-"):
        raise ValueError(f"kebab 转换产生非法选项形态：{name!r} -> {kebab!r}")
    return kebab


def _shape_note(kind: str) -> str:
    """按类型类别给「这行参数在命令行上怎么写」的形态标注（#219 需求 2）。

    为什么只标 array / object：string / integer / boolean 的形态已经由
    `<kind>` 或无值开关本身说明了，再注一遍是噪音；而 array / object 的
    传参形态有真实的分叉经验 —— 模型常把 JSON 数组塞给 CSV 语义的选项
    （`--member_names '["a","b"]'` 被拆坏，清单 §A1），也常在标量上用
    CSV 写法（#219 D2：对 integer/object 不可执行）。help 是模型选写法
    的依据，形态必须写在参数行上、不能只写在实现里。

    文案须与解析行为一致：数组型逗号/JSON/重复三种写法等价
    （consoleTool._split_option_values），object 只收 JSON object 字面量。
    """
    if kind == "array":
        return "（形态：CSV（a,b）、JSON 数组（[\"a\",\"b\"]）或重复出现，三种写法等价；复杂结构建议 --from payload）"
    if kind == "object":
        return "（形态：JSON object（{\"k\":\"v\"}）；复杂结构建议 --from payload）"
    return ""


def describe_param(
    name: str,
    prop: dict,
    *,
    required: bool,
    aliases: tuple[str, ...] = (),
    positional_label: str = "",
) -> str:
    """把一个参数渲染成 help 里的一行（名字 / 类型 / 别名 / 必填 / 一句说明）。

    `aliases` 是该参数的**短别名**（`--kw` 之于 `title_keyword`）。别名必须显式标注：
    否则 help 里只出现真名，照旧习惯敲 `--kw` 的人会以为它不存在；反过来若 help 里
    只写别名，模型又会以为工具参数就叫那个名字。

    `positional_label` 是「该参数在 console 命令行上是**位置参数**」的标注（#220 需求 2）。
    为什么必须有：locator 参数同时是 schema 参数（如 `dept get` 的 `--dept_id`），照着
    help 敲 `dept get --dept_id 7` 会被 `invalid_argument_count` 拒（清单 §A7 实测第一次
    使用必踩）。标注文案由调用方（命令表派生，含命令名与等价写法）传入，本函数只负责
    单点渲染 —— 文案里说的形态必须与解析行为一致（只说清、不放宽，A7 建议 2 不做）。
    """
    kind = param_kind(prop)
    marker = "必填" if required else "可选"
    # #221 需求 3：help 参数行只展示 kebab 形态（--title-keyword），旧下划线名
    # 是兼容期别名、解析层接受但**不在 help 展示**（包括不混排在别名标注里）。
    head = f"--{_kebab(name)} <{kind}>" if kind != "boolean" else f"--{_kebab(name)}"
    if aliases:
        head += "（别名 " + " / ".join("--" + alias for alias in aliases) + "）"
    shape = _shape_note(kind)
    positional = f"（{positional_label}）" if positional_label else ""
    note = _first_clause(prop.get("description"))
    tail = f"（{marker}）" + (f" {note}" if note else "")
    return f"{head}{shape}{positional}{tail}"


def describe_positional(token: str, prop: dict, *, required: bool) -> str:
    """把一个**位置参数**渲染成「位置参数：」段里的一行（方案 B，#323）。

    与 `describe_param` 的分工：位置参数在命令行上没有 `--名字`，它的「名字」就是
    签名里的那个 token（`<id>` / `<container_id>` / `<note>`）。`token` 由调用方
    从**签名渲染的同一处**取（`consoleTool._positional_token`），保证两处逐字一致
    —— 这正是 C-08 的病根：签名印 `<id>`、参数行印 `--dept-id`，同一件事两个 token。

    必填/可选由 `required` 传入，调用方按**命令表的位置 arity**（`min_pos`）判，
    **不按 schema 的 `required`** —— C-09 的根因就是拿了 schema required，导致
    `container note` 两个必填位置槽被印成「（可选）」。
    """
    kind = param_kind(prop)
    marker = "必填" if required else "可选"
    note = _first_clause(prop.get("description"))
    tail = f"（{marker}）" + (f" {note}" if note else "")
    return f"{token} <{kind}>{tail}"


def describe_tool_params(
    tool_name: str,
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
    positional_labels: dict[str, str] | None = None,
) -> list[str]:
    """某工具全部参数的 help 行（schema 顺序）；schema 取不到返回空列表。

    `aliases` 是「真名 → 别名元组」的反向映射（命令行才有别名，schema 里没有），
    由命令表按 `CommandSpec.key` 提供。`positional_labels` 同理：真名 → 「位置参数」
    标注文案（#220），由 consoleTool 按命令表派生；schema 层不知道命令表，
    所以由调用方传进来而不是在这里再查一份。
    """
    alias_map = aliases or {}
    label_map = positional_labels or {}
    required = set(required_params(tool_name))
    return [
        describe_param(
            name,
            prop,
            required=name in required,
            aliases=alias_map.get(name, ()),
            positional_label=label_map.get(name, ""),
        )
        for name, prop in param_schemas(tool_name).items()
    ]
