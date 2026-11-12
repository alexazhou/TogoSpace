"""`console` 的 **schema 派生层**：把 legacy tool 的参数 JSON Schema 翻译成命令行形态。

## 为什么需要这一层（而不是继续手写）

#196 P2 的验收标准之一是「加一条新命令只改命令表一处；help 自动生成，有一致性护栏
测试」。手写 help 与手写 `options` 白名单在两处各存一份事实：

- `tools.py` 的签名/docstring 才是**模型看到的参数面**（`get_function_metadata` 生成的就是
  下发给模型的 schema，同源）；
- `consoleTool.HELP_DETAIL` / `consoleCommands.CommandSpec.options` 是手抄副本。

副本与事实之间没有任何机械约束，于是「加了参数但 help 没写」「改了参数名但选项名没改」
都不会让任何用例变红，只能靠人记得。`taskService` 曾有手写 `clear_fields` 副本遮蔽派生
常量、变异自证照样红但红错理由（#181 现场）——同一类陷阱。

所以本模块**只做一件事**：从 schema 单向派生。派生物一律运行时算，不做快照。

## 为什么依赖延迟到函数内 import

`consoleCommands` 的设计约束是「只依赖标准库，单元测试可脱离 DB 直接跑」。本层必须
import `tools`（那里才有被 schema 化的函数本体），所以把 import 全部放进函数体：
模块顶层仍然只有标准库，`import consoleSchema` 不会拉起任何 service 依赖。

## 与 #189 校验层的分工（不重复造第二份校验）

本层只回答「命令行怎么写这个参数」：叫什么、类型属于哪一类（integer / boolean /
array / string / object）、是否必填。**不校验取值合法性**（枚举、正数范围、业务规则）——
那是 #189 第 2 跳 schema 校验 + 服务层的职责。在这里再做一遍就是第二个行为源，
扩枚举时会漂移（#187 裁定③的同一逻辑）。
"""

from __future__ import annotations

import functools
from typing import Any, Optional

#: 执行上下文注入参数——不是模型可见参数，一律不派生。
_SKIPPED_PARAMS = ("_context",)

#: 参数说明截断长度（help 里每个参数一行，太长会把 help 撑回「全量 schema」的体积）。
_DESCRIPTION_MAX = 48


@functools.lru_cache(maxsize=None)
def tool_parameters(tool_name: str) -> Optional[dict]:
    """取某工具的 `parameters` 子 schema；取不到返回 `None`（调用方降级，不抛异常）。

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
    params = metadata.get("parameters") if isinstance(metadata, dict) else None
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
    params = tool_parameters(tool_name)
    if not params:
        return ""
    from . import tools as legacy_tools
    from .funcToolType import get_function_metadata

    func = getattr(legacy_tools, tool_name, None)
    if func is None:
        return ""
    try:
        metadata = get_function_metadata(tool_name, func)
    except Exception:  # noqa: BLE001 —— 与 tool_parameters 同口径：派生失败只降级
        return ""
    return _first_clause(metadata.get("description") if isinstance(metadata, dict) else "")


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


def describe_param(
    name: str,
    prop: dict,
    *,
    required: bool,
    aliases: tuple[str, ...] = (),
) -> str:
    """把一个参数渲染成 help 里的一行（名字 / 类型 / 别名 / 必填 / 一句说明）。

    `aliases` 是该参数的**短别名**（`--kw` 之于 `title_keyword`）。别名必须显式标注：
    否则 help 里只出现真名，照旧习惯敲 `--kw` 的人会以为它不存在；反过来若 help 里
    只写别名，模型又会以为工具参数就叫那个名字。
    """
    kind = param_kind(prop)
    marker = "必填" if required else "可选"
    head = f"--{name} <{kind}>" if kind != "boolean" else f"--{name}"
    if aliases:
        head += "（别名 " + " / ".join("--" + alias for alias in aliases) + "）"
    note = _first_clause(prop.get("description"))
    tail = f"（{marker}）" + (f" {note}" if note else "")
    return f"{head}{tail}"


def describe_tool_params(tool_name: str, *, aliases: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """某工具全部参数的 help 行（schema 顺序）；schema 取不到返回空列表。

    `aliases` 是「真名 → 别名元组」的反向映射（命令行才有别名，schema 里没有），
    由命令表按 `CommandSpec.key` 提供。
    """
    alias_map = aliases or {}
    required = set(required_params(tool_name))
    return [
        describe_param(name, prop, required=name in required, aliases=alias_map.get(name, ()))
        for name, prop in param_schemas(tool_name).items()
    ]
