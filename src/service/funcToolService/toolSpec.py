"""工具声明层：一个工具的全部非 schema 元数据，与签名 / docstring 同处声明。

设计依据：`docs/design/2026-11-20-console-tool-metadata/console_tool_metadata_design.md`
§3。收拢的五类事实：

| 事实 | 旧位置 | 现位置 |
| --- | --- | --- |
| 可见性 | `core._declared_visibility` + `_EXPLICIT_VISIBILITY` + `_CONTAINER_TOOL_VISIBILITY` | `ToolSpec.visibility` |
| D2 收敛归属 | `consoleCommands.LEGACY_CONVERGED_TOOLS` | 同上（D2 关闭时所有 `BOTH` 统一降级） |
| 分组（默认 / 容器） | `core.CONTAINER_TOOL_NAMES` | `ToolSpec.group` |
| 自中断 | driver 里 `fn_name == "reload_team"` + `core._SELF_INTERRUPT_PREDICATES` | `ToolSpec.self_interrupt` / `self_interrupt_predicate` |
| console 绑定与 help 补充 | `consoleCommands.COMMAND_SPECS` / `_OPTION_ALIASES` | `ToolSpec.console` / `ToolSpec.help`（`_HELP_OVERLAY` 残余条目的口径见该常量注释） |

**模块顶层只依赖 `constants` 与标准库**（设计 §4.1 C-c）：`tools.py` 直接 import 本模块
挂装饰器，因此这里绝不能反过来 import `tools` / `core` / `consoleRegistry`，否则会与
「`core` → `tools` → `toolSpec`」形成循环。D2 开关与可见性解析是纯函数，`core` 反向
import 本模块（设计 §4.1 C-b），不再由 `core` 自己持有那份表。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from constants import ToolVisibility

# ---------------------------------------------------------------------------
# D2 单点开关（原 `consoleCommands.ENV_KEEP_LEGACY_TOOLS`）
#
# 为什么用环境变量而不是模块级常量：测 token 收益时要**在不改代码**的前提下对比两种
# 暴露面，环境变量能让同一次构建跑两个配置。默认值 = 保留；任何无法识别的值也按保留
# 处理 ——「悄悄收走一批工具」的后果比「多暴露几个工具」严重得多。
# ---------------------------------------------------------------------------
ENV_KEEP_LEGACY_TOOLS = "TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS"

F = TypeVar("F", bound=Callable[..., Any])


def keep_legacy_admin_tools() -> bool:
    """D2 单点开关：被 `console` 收敛的工具是否仍作为独立 Tool 暴露。

    只影响**模型暴露面**（`ToolVisibility.tool_visible`）；注册面与 console 的转发面
    不受影响：三个 Driver 用 `get_all_tools()` 全量注册，`get_tools()` 只保留
    `tool_visible` 的工具，`execute_tool_call` 第 2 跳只要求 `console_visible`。
    """
    raw = os.environ.get(ENV_KEEP_LEGACY_TOOLS, "")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def resolve_effective_visibility(declared: ToolVisibility) -> ToolVisibility:
    """静态声明 → 当前环境下的实际可见性（**纯函数**，不查注册表）。

    设计 §4.3：D2 关闭时**所有** `BOTH` 工具统一降为 `CONSOLE_ONLY`，不再按工具名
    特例豁免（废止 #187 裁定②：`task` 四工具与 MCP 注册表四工具同样收敛）。
    把「哪些工具收敛」从一张名单改成一条规则，是为了让新增工具不可能被漏登记。

    入参只有 `declared`（旧签名是 `(name, declared)`）：用户裁决「按名字特例」这件事
    本身要废止，保留 `name` 只会诱导下一个人往回加名单。
    """
    if declared == ToolVisibility.BOTH and not keep_legacy_admin_tools():
        return ToolVisibility.CONSOLE_ONLY
    return declared


# ---------------------------------------------------------------------------
# 数据模型（设计 §3.1）
#
# 组合优先于继承：`Target` 是 `Arg` 的属性，`Arg` 是 `ConsoleCommand` 的属性，
# `ConsoleCommand` 是 `ToolSpec` 的属性。取消 `Verb` / `Locator` / `bind` /
# `input_defaults` / `filter_options` / `fallback_options` / `mechanism_params` /
# `min_args` / `max_args` / `needs_target` —— 这些要么由 `Arg` 的槽位属性表达，
# 要么归回 Python 工具函数（设计 §2.2）。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """值映射目标：将 CLI 输入转换为指定工具参数 `name`。

    Attributes:
        name: 映射到的 Python 工具参数名（必须在该工具 schema 内，`build()` 校验）。
        type: 期望类型（`int` / `str`，或 `None` 表示从工具 schema 派生
            `integer → int` / `string → str`；派生不出则由 `build()` 报错）。
    """

    name: str
    type: type[int] | type[str] | None = None


@dataclass(frozen=True)
class Arg:
    """CLI 参数槽位（参考 Click / Typer / Clap）。

    Attributes:
        target: 该槽位映射到的工具参数（严格 1:1，不做隐式类型猜测）。
        required: `True`=该 CLI 槽位必选。工具签名里无默认值的参数即便这里写
            `False`，也会在第 2 跳 schema 校验被要求必填。
        positional: `True`=位置参数（按声明顺序占槽位 0,1,2...）；`False`=`--选项`。
        alias: 可选短别名（`"kw"` → `title_keyword`）；只对 `--选项` 有意义。
    """

    target: Target
    required: bool = False
    positional: bool = False
    alias: str | None = None


@dataclass(frozen=True)
class ConsoleCommand:
    """工具挂到 console 上的一条子命令。

    Attributes:
        resource: 资源词（`dept` / `task`）；顶层命令直接填命令名（如 `reload`）。
        action: 动作词：单个 `str` 或元组（首个为主名用于 help 展示，其余为别名，
            如 `("rm", "remove")`）；顶层命令留空 `""`。
        args: 位置槽与别名声明；未列出的函数参数默认作为 `--选项` 自动暴露。
        payload: 是否接受整包 JSON（尾部内联 `'<json>'` 或 `--from payload`）。
        help: 命令级 help 补充（只进 console help，不进模型 schema）。
        exclude: 该命令**不暴露也不要**的工具参数名（D1=A / #323）。一工具多命令时用它
            裁剪参数子集：被排除的参数既不出现在 help 参数清单里，也不在解析白名单里
            （传了就是 `unknown_option`）。只能排除**未声明且非位置槽**的 schema 参数
            —— 位置槽要收窄请改 `Arg.required`。
        locators: 同一组**互斥定位参数**（#334 / C-16 家族）。列出的工具参数属于同一
            定位语义，调用时**最多一个来源**（位置槽 / `--选项` / payload 三处合起来计数）：
            同时给出 >1 个就在入口报 `ambiguous_input_source`，不再让底层 `if id is not None`
            静默丢掉另一个。只对「真的支持二选一」的命令声明（如 `dept rm` / `room rm` 的
            `<id>` 与 `--name`）；单一必填定位（`dept get <id>`）不要声明——它的 `--name`
            已在 #323 裁剪掉，写了也没人传得进来。
    """

    resource: str
    action: str | tuple[str, ...] = ""
    args: tuple[Arg, ...] = ()
    payload: bool = False
    help: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    locators: tuple[str, ...] = ()

    @property
    def primary_action(self) -> str:
        """主名（help 展示与 `by_resource` 只认它）。"""
        return self.action[0] if isinstance(self.action, tuple) else self.action

    @property
    def actions(self) -> tuple[str, ...]:
        """全部动作词（主名 + 别名）。"""
        if isinstance(self.action, tuple):
            return self.action
        return (self.action,) if self.action else ()

    @property
    def key(self) -> str:
        """主命令路径（`dept update` / `reload`）。"""
        return f"{self.resource} {self.primary_action}".strip()

    @property
    def keys(self) -> tuple[str, ...]:
        """全部可路由路径（主名 + 别名各一条；顶层命令就是资源词本身）。"""
        return tuple(f"{self.resource} {a}" for a in self.actions) if self.actions else (self.resource,)

    @property
    def positional_args(self) -> tuple[Arg, ...]:
        """位置槽（声明顺序即槽位顺序）。"""
        return tuple(arg for arg in self.args if arg.positional)


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明（`@tool(...)` 的参数即本结构的字段）。

    Attributes:
        visibility: 静态基准可见性；受 D2 开关控制的工具在 `core` 层按
            `resolve_effective_visibility()` 动态解析。
        self_interrupt: 静态自中断（取代 driver 里 `fn_name == "reload_team"`）。
        self_interrupt_predicate: 动态自中断判定器（console 专用）。
        group: `"default"` 或 `"container"`（取代 `CONTAINER_TOOL_NAMES`）。
        help: 工具级 help 补充（只进 console help，不进模型 schema）。
        console: 挂到 console 上的子命令（一个工具可挂多条，如 `dept ls` / `dept get`）。
    """

    visibility: ToolVisibility = ToolVisibility.TOOL_ONLY
    self_interrupt: bool = False
    self_interrupt_predicate: Callable[[Any], bool] | None = None
    group: str = "default"
    help: tuple[str, ...] = ()
    console: tuple[ConsoleCommand, ...] = ()

    @property
    def console_bound(self) -> bool:
        """是否挂了 console 命令（决定 `console_visible` 是否必须成立）。"""
        return bool(self.console)


# ---------------------------------------------------------------------------
# 声明收集（设计 §4.1 C-b）
#
# `@tool(...)` 只做两件事：把 `ToolSpec` 挂到函数对象上（`__tool_spec__`），并登记到
# 本模块的收集列表。**不包装函数**——`FuncTool` 靠签名反射生成 schema，包一层
# `functools.wraps` 的 wrapper 会让 `inspect.signature` 拿到 `*args, **kwargs`。
#
# 收集列表用 `dict[函数名, ToolSpec]` 而不是 list：函数名就是注册名，重复登记必须
# 当场炸（同一个名字出现两次 ⇒ console 命令表 / 注册表会出现两条同名工具，第 2 跳
# 按名字查谁都不确定）。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeclaredTool:
    """一条声明：注册名 + 工具函数本体 + 声明。

    收集列表同时留函数本体，是因为 `consoleRegistry.build()` 要按函数签名派生 schema，
    而「按名字回 `tools` 模块 `getattr`」正是本设计要消灭的绕路（设计 §1.1 第 6 条）。
    """

    name: str
    func: Callable[..., Any]
    spec: ToolSpec


_REGISTERED: dict[str, DeclaredTool] = {}


def tool(
    *,
    visibility: ToolVisibility = ToolVisibility.TOOL_ONLY,
    self_interrupt: bool = False,
    self_interrupt_predicate: Callable[[Any], bool] | None = None,
    group: str = "default",
    help: tuple[str, ...] = (),
    console: tuple[ConsoleCommand, ...] = (),
) -> Callable[[F], F]:
    """把声明挂到工具函数上并登记（**不包装函数**，签名反射零影响）。"""

    spec = ToolSpec(
        visibility=visibility,
        self_interrupt=self_interrupt,
        self_interrupt_predicate=self_interrupt_predicate,
        group=group,
        help=help,
        console=console,
    )

    def _decorate(func: F) -> F:
        name = func.__name__
        existing = _REGISTERED.get(name)
        if existing is not None:
            raise RuntimeError(
                f"工具 {name!r} 被 @tool 重复声明：同一注册名只能声明一次"
                "（否则 console 命令表与注册表会出现两条同名工具）。"
            )
        func.__tool_spec__ = spec  # type: ignore[attr-defined]
        _REGISTERED[name] = DeclaredTool(name=name, func=func, spec=spec)
        return func

    return _decorate


def declared_tools() -> tuple[DeclaredTool, ...]:
    """已声明的工具快照（按声明顺序）。

    返回 `tuple` 而不是内部 dict：调用方只该读。`consoleRegistry.build()` 拿它构造
    `FuncTool`，**不反向调用 `core`**（设计 §4.1 C-b）。
    """
    return tuple(_REGISTERED.values())


def declared_specs() -> tuple[tuple[str, ToolSpec], ...]:
    """`(注册名, 声明)` 快照（只要声明、不要函数本体的调用方用）。"""
    return tuple((item.name, item.spec) for item in _REGISTERED.values())


def get_declared_spec(name: str) -> ToolSpec | None:
    """按注册名取声明；未声明返回 `None`（调用方决定 fail-closed 还是报错）。"""
    declared = _REGISTERED.get(name)
    return declared.spec if declared is not None else None


def spec_of(func: Callable[..., Any]) -> ToolSpec:
    """取函数上的声明；没有声明说明漏挂 `@tool`（结构性缺陷，直接炸）。"""
    spec = getattr(func, "__tool_spec__", None)
    if not isinstance(spec, ToolSpec):
        raise RuntimeError(
            f"工具函数 {getattr(func, '__name__', func)!r} 没有 @tool 声明；"
            "49 个工具必须全部声明（设计 §6 P0-②），漏挂会让可见性与 console 绑定凭空消失。"
        )
    return spec
