"""console 命令表：由工具声明**惰性自举**的派生物（设计 §4）。

本模块只有一件事：把 `@tool(...)` 里的 `ConsoleCommand` 声明 + 工具函数签名，变成
「运行时可路由、可渲染 help 的命令表」。console 从这一刻起**不认识任何具体工具名**：

| 旧实现 | 现实现 |
| --- | --- |
| `consoleCommands.COMMAND_SPECS`（手抄 34 条） | `build(declared_tools())` 派生 |
| `consoleCommands._OPTION_ALIASES`（按命令 key 索引） | `Arg.alias`（跟着声明走） |
| `consoleSchema.param_schemas(tool_name)`（`getattr(tools, name)`） | `ResolvedCommand.tool.callable` 的签名 |
| `core._declared_visibility` / `LEGACY_CONVERGED_TOOLS` / `CONTAINER_TOOL_NAMES` | `ToolSpec.visibility` / `ToolSpec.group` |

## 三条防循环硬约束（设计 §4.1 C-a / C-b / C-c）

- **C-b**：`build()` 是**纯函数**（不碰 `core`、不写全局），`get_table()` 才负责「确保
  `tools` / `consoleTool` 已完整导入 → 按 `toolSpec` 收集列表建表」。单测构造假工具
  一律直接调 `build()`；`clear_cache()` 供测试重置。
- **C-a/C-c**：本模块顶层**不 import `core` / `tools`**（`tools` 在 `get_table()` 内延迟
  import），help 的 `HELP_SUMMARY` / `HELP_DETAIL` 由 `consoleTool` 用 PEP 562 惰性
  生成，不再在 import 期立即求值。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from constants import ToolVisibility

from .consoleCommands import to_kebab
from .consoleSchema import param_kind
from .funcToolType import FuncTool, get_function_metadata
from .toolSpec import Arg, ConsoleCommand, ToolSpec, declared_tools, resolve_effective_visibility

#: 资源词的**展示顺序**（help 摘要与 `by_resource` 只影响可读性，不影响路由）。
#: 放在这里而不是由 `tools.py` 的声明顺序决定：声明顺序会随代码整理抖动，help 每抖一次
#: 都像「命令变了」，而模型看到的摘要会跟着变。
RESOURCE_ORDER: tuple[str, ...] = ("dept", "agent", "room", "role", "task", "container", "time", "reload")

#: 动作词的**展示顺序**（`ls / get / create / update / rm / 其余`）。
ACTION_ORDER: tuple[str, ...] = ("ls", "get", "create", "update", "rm")

#: 容器组工具数量（P0 的 49 = 43 普通 + 6 容器）。自举完整性校验用。
CONTAINER_GROUP_SIZE = 6


def _action_rank(action: str) -> tuple[int, str]:
    try:
        return (ACTION_ORDER.index(action), action)
    except ValueError:
        return (len(ACTION_ORDER), action)


def _resource_rank(resource: str) -> tuple[int, str]:
    try:
        return (RESOURCE_ORDER.index(resource), resource)
    except ValueError:
        return (len(RESOURCE_ORDER), resource)


@runtime_checkable
class ConsoleExtension(Protocol):
    """不能 1:1 映射到单个工具的子树（`mcp` / `openapi` / `graphql`）的插件协议（§4.4）。

    P0 只定义协议与冲突校验位置：`console()` 仍按既有硬分派调用 `consoleMcp` /
    `consoleSpec`（设计 §6 P0 不做边界明确「P3 才做 extension 化」）。把协议先立在
    注册表里，是为了让 P3 接入时不必再动命令表结构。
    """

    resources: tuple[str, ...]
    summary: str

    def help_payload(self, tokens: list[str]) -> dict[str, Any]:  # pragma: no cover - Protocol
        ...

    async def dispatch(self, command: str, tokens: list[str], ctx: Any) -> dict[str, Any]:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# 运行时命令
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCommand:
    """运行时命令 = 声明 + 所属工具（兼容原 `CommandSpec` 的选项面接口）。

    `tool=None` 只允许出现在纯本地命令（`help`）上：它没有工具、没有 schema、也不转发。
    其余命令必须带 `FuncTool`，否则选项面无从派生、第 2 跳也无处转发。
    """

    decl: ConsoleCommand
    tool: FuncTool | None = None

    # -- 标识 ---------------------------------------------------------------
    @property
    def key(self) -> str:
        return self.decl.key

    @property
    def keys(self) -> tuple[str, ...]:
        return self.decl.keys

    @property
    def resource(self) -> str:
        return self.decl.resource

    @property
    def primary_action(self) -> str:
        return self.decl.primary_action

    @property
    def actions(self) -> tuple[str, ...]:
        return self.decl.actions

    @property
    def payload(self) -> bool:
        """该命令是否接受整包配置输入（`'<json>'` / `--from payload`）。"""
        return self.decl.payload

    @property
    def tool_name(self) -> str:
        """转发目标工具名（`help` 为空串 ⇒ 不转发、不鉴权）。"""
        return self.tool.name if self.tool is not None else ""

    @property
    def spec(self) -> ToolSpec | None:
        return self.tool.spec if self.tool is not None else None

    @property
    def help_lines(self) -> tuple[str, ...]:
        """补充说明：工具级（`ToolSpec.help`）+ 命令级（`ConsoleCommand.help`）。"""
        tool_help = self.spec.help if self.spec is not None else ()
        return tuple(tool_help) + tuple(self.decl.help)

    # -- 位置槽 -------------------------------------------------------------
    @property
    def positional_args(self) -> tuple[Arg, ...]:
        return self.decl.positional_args

    @property
    def min_pos(self) -> int:
        return sum(1 for arg in self.positional_args if arg.required)

    @property
    def max_pos(self) -> int:
        return len(self.positional_args)

    @property
    def positional_param_names(self) -> frozenset[str]:
        """位置槽绑定的工具参数真名（§4.1：直接用 `{a.target.name for a in positional_args}`）。"""
        return frozenset(arg.target.name for arg in self.positional_args)

    # -- schema 派生（来源是 `self.tool.callable`，不是 `getattr(tools, name)`）----
    @functools.cached_property
    def param_schemas(self) -> dict[str, dict]:
        """参数名 → schema 片段（按签名声明顺序，已剔除 `_context`）。"""
        if self.tool is None:
            return {}
        try:
            metadata = get_function_metadata(self.tool.name, self.tool.callable)
        except Exception:  # noqa: BLE001 —— 派生失败只降级，不让 help/路由整块崩
            return {}
        properties = (metadata.get("parameters") or {}).get("properties")
        if not isinstance(properties, dict):
            return {}
        return {name: prop for name, prop in properties.items() if name != "_context" and isinstance(prop, dict)}

    @functools.cached_property
    def param_names(self) -> tuple[str, ...]:
        return tuple(self.param_schemas)

    @functools.cached_property
    def param_kinds(self) -> dict[str, str]:
        return {name: param_kind(prop) for name, prop in self.param_schemas.items()}

    @functools.cached_property
    def schema_required(self) -> tuple[str, ...]:
        """签名层的必填参数（无默认值）—— 按 schema 声明顺序。"""
        if self.tool is None:
            return ()
        try:
            metadata = get_function_metadata(self.tool.name, self.tool.callable)
        except Exception:  # noqa: BLE001
            return ()
        required = (metadata.get("parameters") or {}).get("required")
        if not isinstance(required, list):
            return ()
        return tuple(name for name in required if name != "_context")

    # -- 选项面 -------------------------------------------------------------
    @functools.cached_property
    def alias_map(self) -> dict[str, str]:
        """`Arg.alias` 声明 → 「别名 → 真名」映射（取代 `_OPTION_ALIASES`）。"""
        return {
            arg.alias: arg.target.name
            for arg in self.decl.args
            if arg.alias and not arg.positional
        }

    @functools.cached_property
    def command_options_map(self) -> dict[str, str]:
        """命令行选项形态 → 工具真名（kebab / 兼容旧下划线名 / 声明别名）。

        键的三类来源（**一个真名允许多个命令行形态**，这是兼容期的本义，见
        `consoleCommands.to_kebab` 的移除触发条件）：
        - kebab 形态（`--clear-fields` → `clear_fields`）：help 展示的主形态；
        - 真名形态（`--clear_fields` → `clear_fields`）：兼容期别名；
        - 手写短别名（`--kw` → `title_keyword` / `--remove-field` → `clear_fields`）。
        冲突即炸：静默选边只会让其中一种写法悄悄失效。
        """
        mapping: dict[str, str] = {}

        def _register(form: str, real: str) -> None:
            existing = mapping.get(form)
            if existing is not None and existing != real:
                raise ValueError(f"选项形态 {form!r} 冲突：{existing!r} vs {real!r}（命令 {self.key}）")
            mapping[form] = real

        for real in self.param_names:
            _register(to_kebab(real), real)
            _register(real, real)
        for alias, real in self.alias_map.items():
            _register(alias, real)
            if "_" in alias:
                _register(to_kebab(alias), real)
            if "-" in alias:
                _register(alias.replace("-", "_"), real)
        return mapping

    @property
    def options(self) -> tuple[str, ...]:
        """命令行认可的选项名（判定用；报错展示用 `display_options`）。"""
        return tuple(self.command_options_map)

    @property
    def params(self) -> tuple[str, ...]:
        """同 `options`（help 用法行只关心「有没有选项」）。"""
        return self.options

    def normalize_option(self, name: str) -> str:
        return self.command_options_map.get(name, name)

    @property
    def display_options(self) -> tuple[str, ...]:
        """展示清单（kebab + 别名），不含兼容旧名、**不含位置槽绑定的参数**（§4.1）。"""
        positional = self.positional_param_names
        return tuple(
            form for form, real in self.command_options_map.items() if "_" not in form and real not in positional
        )

    @property
    def boolean_options(self) -> frozenset[str]:
        """不带值即可出现的开关选项（schema `type: boolean` 的那批，含全部命令行形态）。"""
        booleans = {name for name, kind in self.param_kinds.items() if kind == "boolean"}
        result: set[str] = set()
        for real in booleans:
            result.add(real)
            result.add(to_kebab(real))
        for alias, real in self.alias_map.items():
            if real in booleans:
                result.add(alias)
                if "_" in alias:
                    result.add(to_kebab(alias))
        return frozenset(result)

    @property
    def option_kinds(self) -> dict[str, str]:
        """选项形态 → 类型类别（解析层按**用户写下的形态**查，kebab 必须命中）。"""
        kinds = dict(self.param_kinds)
        for form, real in self.command_options_map.items():
            kinds.setdefault(form, kinds.get(real, "string"))
        return kinds

    @property
    def required_option_names(self) -> tuple[str, ...]:
        """必填的**非位置**参数：声明 `Arg(required=True)` ∪ 签名无默认值（§5.1 第 4 步）。"""
        positional = self.positional_param_names
        declared = {arg.target.name for arg in self.decl.args if not arg.positional and arg.required}
        derived = {name for name in self.schema_required if name not in positional}
        ordered = [name for name in self.param_names if name in (declared | derived)]
        return tuple(ordered)

    # -- 兼容旧 `CommandSpec` 的报错文案字段（新模型里由工具/声明各自承担）------
    @property
    def example(self) -> str:
        """未知选项报错里的「正确调用」示例。P0 无命令填写（`mcp call` 已不在本表）。"""
        return ""

    @property
    def option_tip(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# 命令表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandTable:
    """派生出的命令表：`specs` 是全部命令，索引是两份不同用途的视图（§4.1）。

    - `by_path`：**路由**用，索引主名 + 全部 action 别名（`dept rm` 与 `dept remove` 均可）；
    - `by_resource`：**展示**用，只收 `primary_action`，按 `RESOURCE_ORDER` / `ACTION_ORDER`
      排序，避免摘要重复或顺序随声明抖动。
    """

    specs: tuple[ResolvedCommand, ...]
    by_path: dict[str, ResolvedCommand]
    by_resource: dict[str, tuple[ResolvedCommand, ...]]
    help_spec: ResolvedCommand
    extensions: dict[str, ConsoleExtension] | None = None

    # -- 视图 ---------------------------------------------------------------
    @property
    def resources(self) -> tuple[str, ...]:
        return tuple(self.by_resource)

    @property
    def actions_by_resource(self) -> dict[str, tuple[str, ...]]:
        return {
            resource: tuple(cmd.primary_action for cmd in commands if cmd.primary_action)
            for resource, commands in self.by_resource.items()
        }

    def resolve(self, tokens: list[str]) -> tuple[ResolvedCommand | None, str | None]:
        """从 token 里认出命令 → `(命令, 错误信息)`（两段式 + 顶层命令）。"""
        if not tokens:
            return None, "命令为空"
        head = tokens[0].lower()
        if head in ("help", "?"):
            return self.help_spec, None
        if len(tokens) > 1:
            cmd = self.by_path.get(f"{head} {tokens[1].lower()}")
            if cmd is not None:
                return cmd, None
        top = self.by_path.get(head)
        if top is not None and not top.actions:
            return top, None
        actions = self.actions_by_resource.get(head)
        if actions is None:
            return None, f"未知资源：'{head}'。可用资源：{' | '.join(self.resources)}"
        second = tokens[1] if len(tokens) > 1 else ""
        return None, f"未知命令：'{head} {second}'。可用操作：{' | '.join(actions)}"

    def visible_specs(self) -> tuple[ResolvedCommand, ...]:
        """当前可通过 console 使用的命令（运行时按 `console_visible` 过滤）。"""
        return tuple(cmd for cmd in self.specs if command_console_visible(cmd))


def command_console_visible(cmd: ResolvedCommand) -> bool:
    """该命令当前是否可用 = 其工具的 `effective_visibility.console_visible`（运行时判定）。

    纯本地命令（`tool is None`）恒可用：它不碰数据、不鉴权。运行时判定而不是 import
    期固化，是因为 D2 开关可以在同一进程内被切换（测试就依赖这一点）。
    """
    if cmd.tool is None:
        return True
    return resolve_effective_visibility(cmd.tool.visibility).console_visible


HELP_SPEC = ResolvedCommand(decl=ConsoleCommand("help"), tool=None)


# ---------------------------------------------------------------------------
# 构建（纯函数）+ 校验（fail-fast）
# ---------------------------------------------------------------------------


def _validate_arg(cmd: ResolvedCommand, arg: Arg, *, seen_positional: list[Arg]) -> None:
    """单个参数的声明一致性校验（§4.2「参数与类型一致性」「位置参数与别名约束」）。"""
    name = arg.target.name
    if name not in cmd.param_schemas:
        raise ValueError(
            f"`{cmd.key}` 的参数声明引用了不存在的工具参数 {name!r}；"
            f"该工具的参数是：{' / '.join(cmd.param_names) or '（无）'}。"
        )
    schema_kind = cmd.param_kinds.get(name, "string")
    declared = arg.target.type
    if declared not in (None, int, str):
        raise ValueError(f"`{cmd.key}` 的 {name!r} 声明了不支持的类型 {declared!r}（只允许 int / str / None）。")
    # 类型一致性只对**位置槽**强制：位置参数没有 `--选项` 的取值形态，转换规则只能
    # 来自 `Target.type` / schema；而 `--选项` 的类型由 schema 的 `option_kinds` 决定
    # （array / object / boolean 都能落在选项上），`Target.type` 对它没有语义 ——
    # 强行要求声明类型会把「只想给 array 参数起个别名」变成不可能。
    if arg.positional:
        if declared is None:
            if schema_kind not in ("integer", "string"):
                raise ValueError(
                    f"`{cmd.key}` 的位置参数 {name!r} 未声明类型，且无法从 schema 派生"
                    f"（schema 类型 {schema_kind!r} 既不是 integer 也不是 string）。"
                )
        else:
            expected = "integer" if declared is int else "string"
            if schema_kind != expected:
                raise ValueError(
                    f"`{cmd.key}` 的位置参数 {name!r} 声明类型 {declared.__name__} 与 schema 类型 {schema_kind!r} 不一致。"
                )
    if arg.alias:
        if arg.positional:
            raise ValueError(f"`{cmd.key}` 的 {name!r} 是位置参数，不能声明别名 {arg.alias!r}。")
        if arg.alias in cmd.param_names:
            raise ValueError(f"`{cmd.key}` 的别名 {arg.alias!r} 撞了真参数名。")
        if to_kebab(arg.alias) in {to_kebab(real) for real in cmd.param_names}:
            raise ValueError(f"`{cmd.key}` 的别名 {arg.alias!r} kebab 后撞了真参数名。")
    if arg.positional:
        if seen_positional and not seen_positional[-1].required and arg.required:
            raise ValueError(
                f"`{cmd.key}` 在可选位置参数（{seen_positional[-1].target.name!r}）之后又声明了必填位置参数 {name!r}，"
                "槽位顺序会有二义性。"
            )
        order = {param: index for index, param in enumerate(cmd.param_names)}
        if seen_positional and order.get(name, -1) < order.get(seen_positional[-1].target.name, -1):
            raise ValueError(
                f"`{cmd.key}` 的位置参数顺序 {name!r} 与函数签名顺序不一致"
                f"（{seen_positional[-1].target.name!r} 之后不应再出现 {name!r}）。"
            )
        seen_positional.append(arg)


def _validate_command(cmd: ResolvedCommand, *, seen_keys: dict[str, str], two_segment_resources: set[str],
                      extension_resources: set[str]) -> None:
    """单条命令的声明一致性校验（§4.2 全部 fail-fast 规则）。"""
    decl = cmd.decl
    if isinstance(decl.action, tuple) and not decl.action:
        raise ValueError(f"资源 {decl.resource!r} 的 action 元组为空：元组必须至少有一个动作名。")
    if not decl.resource:
        raise ValueError("ConsoleCommand 的 resource 不能为空。")
    for key in cmd.keys:
        if key in seen_keys:
            raise ValueError(f"命令路径 {key!r} 重复声明（{seen_keys[key]!r} 与 {cmd.tool_name!r}）。")
        seen_keys[key] = cmd.tool_name
    if not cmd.actions:
        # 顶层命令（`reload`）：不许与两段式资源词或扩展资源撞名。
        if cmd.resource in two_segment_resources:
            raise ValueError(f"顶层命令 {cmd.resource!r} 与同名资源冲突（该资源下还有子命令）。")
        if cmd.resource in extension_resources:
            raise ValueError(f"顶层命令 {cmd.resource!r} 与 console 扩展资源冲突。")
    if cmd.tool is None:
        return
    if cmd.spec is not None and not resolve_effective_visibility(cmd.tool.visibility).console_visible:
        raise ValueError(
            f"工具 {cmd.tool_name!r} 挂了 console 命令（{cmd.key}），但声明的可见性 "
            f"{cmd.tool.visibility.name} 不可通过 console 使用。"
        )
    seen_positional: list[Arg] = []
    for arg in decl.args:
        _validate_arg(cmd, arg, seen_positional=seen_positional)
    if decl.payload:
        non_positional = [arg for arg in decl.args if not arg.positional]
        if not non_positional:
            # 声明里没写非位置参数时，工具签名里可能还有；只有「一个都没有」才是错。
            if not (set(cmd.param_names) - cmd.positional_param_names):
                raise ValueError(
                    f"`{cmd.key}` 声明了 payload=True，但工具没有任何非位置参数——整包 JSON 无处可放。"
                )
        if any(arg.positional and not arg.required for arg in decl.args):
            raise ValueError(
                f"`{cmd.key}` 同时声明了 payload=True 与可选位置参数：尾部内联 JSON 与该槽位有歧义。"
            )


def build(
    func_tools: Iterable[FuncTool],
    extensions: Iterable[ConsoleExtension] = (),
    *,
    strict: bool = False,
) -> CommandTable:
    """由 `FuncTool`（必须带 `spec`）+ 扩展构造命令表（**纯函数**，设计 §4.1 C-b）。

    Args:
        func_tools: 全部工具（普通 + 容器两组都要传，否则容器命令会缺席）。
        extensions: `mcp` / `openapi` / `graphql` 这类子树插件（P0 为空）。
        strict: 是否做自举完整性校验。只有默认的 `get_table()` 全量构建传 True——
            单测构造的假工具集本来就不含 49 个工具，做完整性校验只会误伤。它要挡的
            是「模块半初始化时抢跑建表」：漏了 `console` 自身或容器组不足 6 个，
            说明某处 import 顺序错了，此时建出的表会静默少命令。
    """
    extension_list = list(extensions)
    extension_resources = {resource for ext in extension_list for resource in ext.resources}
    if len(set(extension_resources)) != len(extension_resources):
        raise ValueError(f"console 扩展资源重复：{sorted(extension_resources)}")

    tools = list(func_tools)
    if strict:
        names = {tool.name for tool in tools}
        if "console" not in names:
            raise ValueError("自举完整性校验失败：收集列表里没有 `console` 自身（模块半初始化？）。")
        container_count = sum(1 for tool in tools if _group_of(tool) == "container")
        if container_count != CONTAINER_GROUP_SIZE:
            raise ValueError(
                f"自举完整性校验失败：group=='container' 的工具应为 {CONTAINER_GROUP_SIZE} 个，"
                f"实际 {container_count} 个（模块半初始化？）。"
            )

    seen_keys: dict[str, str] = {}
    two_segment_resources: set[str] = set()
    pending: list[ResolvedCommand] = []
    for tool in tools:
        spec = _spec_of(tool)
        if spec is None:
            continue
        for decl in spec.console:
            cmd = ResolvedCommand(decl=decl, tool=tool)
            if cmd.actions:
                two_segment_resources.add(cmd.resource)
            pending.append(cmd)

    for cmd in pending:
        _validate_command(
            cmd,
            seen_keys=seen_keys,
            two_segment_resources=two_segment_resources,
            extension_resources=extension_resources,
        )
    for resource in extension_resources:
        if resource in two_segment_resources:
            raise ValueError(f"扩展资源 {resource!r} 与命令表里的资源词冲突。")

    # 命令表按「预设资源顺序 + 动作顺序」规范化：help 摘要、`ACTIONS_BY_RESOURCE` 与
    # 报错候选都从这份顺序派生，声明顺序（= `tools.py` 里函数的摆放）不该影响可读性。
    specs = tuple(sorted(pending, key=lambda item: (_resource_rank(item.resource), _action_rank(item.primary_action))))
    by_path: dict[str, ResolvedCommand] = {}
    for cmd in specs:
        for key in cmd.keys:
            by_path[key] = cmd

    grouped: dict[str, list[ResolvedCommand]] = {}
    for cmd in specs:
        grouped.setdefault(cmd.resource, []).append(cmd)

    return CommandTable(
        specs=specs,
        by_path=by_path,
        by_resource={resource: tuple(commands) for resource, commands in grouped.items()},
        help_spec=HELP_SPEC,
        extensions={resource: ext for ext in extension_list for resource in ext.resources},
    )


def _spec_of(tool: FuncTool) -> ToolSpec | None:
    """取工具声明；没有声明返回 `None`（`build` 允许混入未声明工具，`core` 不允许）。"""
    return tool.spec


def _group_of(tool: FuncTool) -> str:
    spec = _spec_of(tool)
    return spec.group if spec is not None else "default"


# ---------------------------------------------------------------------------
# 惰性自举（C-b：只确保 tools / consoleTool 已导入，绝不反向调用 core）
# ---------------------------------------------------------------------------

_TABLE: CommandTable | None = None


def get_table() -> CommandTable:
    """默认命令表（首次访问时自举，进程内复用）。

    自举只做「确保声明都已执行」：`import tools` 的副作用就是 49 个 `@tool(...)` 注册，
    而 `console` 自身在 `consoleTool` 里声明 —— 两者都不 import `core`。
    """
    global _TABLE
    if _TABLE is None:
        from . import consoleTool as _console_tool  # noqa: F401 —— 只为触发 console 的声明
        from . import tools as _tools_module  # noqa: F401 —— 只为触发 49 个 @tool 注册

        func_tools = [
            FuncTool(name=item.name, callable=item.func, visibility=item.spec.visibility, spec=item.spec)
            for item in declared_tools()
        ]
        _TABLE = build(func_tools, strict=True)
    return _TABLE


def clear_cache() -> None:
    """清空缓存（测试用：D2 开关或声明变化后需要重建）。"""
    global _TABLE
    _TABLE = None
