"""console 工具的静态部分：命令表、转发目标、动作语义。

拆成独立模块的原因有三个（都是硬理由，不是「分层好看」）：
1. **「命令 → 转发目标」必须是一张纯数据表**（#160）。权限判定现在完全由唯一
   执行入口 `AgentToolRegistry.execute_tool_call` 在第 2 跳做，命令表只负责声明
   这条命令等价于哪个原工具（`legacy_tool`）。加命令时**必须同时填等价工具名**，
   漏填会让转发目标为空、命令直接不可用，而不是「没填就放行」。
2. **help 文本要能单独量体积**（#154 CC6）。它是唯一额外注入 context 的文本，
   跟解析逻辑混在一个文件里会让「help 有多大」这件事变得难以独立测。
3. **命令表要能承载「动作语义」**（#196 P2）。`verb`（GET / LIST / SAVE / DELETE /
   DIRECT）决定这条命令交给哪个通用处理器，`locator` 决定位置参数 target 落到哪个
   工具参数上。加一条命令只改这一张表：help 行、选项白名单、布尔开关判定、选项值
   类型转换全部由本表 + `consoleSchema` 派生，不再有第三份手抄副本。

依赖：模块顶层只有标准库。选项面（`options` / `boolean_options` / `option_kinds` /
`param_names`）做成了 **property**，只在被真正访问时才 import `consoleSchema`；
而 `consoleSchema` 自己的 `tools` import 又延迟到函数体内。因此
`import consoleCommands`（含构造 `COMMAND_SPECS`）不拉起任何 service 模块，
单元测试仍可脱离 DB 直接跑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# 单点开关（#154 D2）：原 16 个工具是否保留。
#
# 为什么用环境变量而不是模块级常量：测 token 收益时要**在不改代码**的前提下对比
# 两种暴露面，环境变量能让同一次构建跑两个配置。默认值 = 保留，切换主干暴露面
# 由 Operator 决定，不在 #154 范围。
#
# 开关现在只影响**模型暴露面**：已收敛工具的动态可见性在 `BOTH` 与 `CONSOLE_ONLY`
# 之间切换。三个 Driver 用 `get_all_tools()` 全量注册，console 的第 2 跳因此始终
# 找得到转发目标（#166-A 已修）；`help` 与路由也按 `console_visible` 运行时过滤。
# ---------------------------------------------------------------------------
ENV_KEEP_LEGACY_TOOLS = "TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS"

_CONSOLE_NAME = "console"

#: 被 `console` 收敛的 16 个原工具名（draft §1 的清单，逐条对齐）。
#:
#: ⚠️ **本清单目前未包含任务三工具**（`list_tasks` / `get_task_detail` / `update_task`），
#: 而 `task ls|get|update` 三条命令已经把这三者作为转发目标接进来了（#187）。
#: 也就是说：D2 开关置假（隐藏原工具）时，task 三条命令**仍可达**——因为它们的转发
#: 目标不在本清单里，不会被隐藏。
#:
#: 这不是「不自洽」，性质是**未完成的收敛**：本清单是 D2 开关的语义载体，一旦改动
#: 就等于改变所有启用 console 的 Agent 的工具暴露面，属 draft §1 已定死的口径，
#: 须 Operator 决策，不能由实现单方面扩表（#187 裁定②）。故此处**保持 16 项不动**，
#: 只留注释与报告里的一条「只报不改」记录，供后续收敛时一并处理。
LEGACY_CONVERGED_TOOLS: tuple[str, ...] = (
    "get_dept_info",
    "save_dept",
    "delete_dept",
    "get_agent_info",
    "save_agent",
    "wake_up_agent",
    "start_chat",
    "get_room_info",
    "save_room",
    "delete_room",
    "list_role_templates",
    "get_role_template",
    "save_role_template",
    "delete_role_template",
    "get_time",
    "reload_team",
)


InputSource = Literal["inline", "payload", "local", "heredoc"]

#: 命令的动作语义（#196 P2）。前四类各对应一个通用处理器，`DIRECT` 是「位置参数
#: 就是工具参数、没有定位/patch 语义」的直通命令（`agent wake` / `agent dm` /
#: `reload` / `help`）。
#:
#: 为什么要有这一层：P2 之前 `_route` 是一条 200 行的 `if key == "dept rm":` 长链，
#: 每条命令的定位/补丁/预演逻辑各写一遍。同一种语义出现 5 次就会漂移 5 次，于是
#: 只能靠 `test_p165_c` 那种「数 `_resolve_` 调用次数」的结构测试去兜——那是把
#: 症状写进测试。把语义提到命令表上，处理器只有 5 个，加命令不必再动处理器。
Verb = Literal["GET", "LIST", "SAVE", "DELETE", "DIRECT"]


@dataclass(frozen=True)
class Locator:
    """位置参数 target 如何落到底层工具参数上（#196 P2 D8）。

    Attributes:
        id_param: 「数字形态的 target」落到哪个工具参数（`int`）。空表示这条命令
            不做 id 定位。
        name_param: 「非数字形态的 target」落到哪个工具参数（`str`）。空表示这条
            命令不做名称定位。

    两者组合出的三种定位形态：
        - `id` + `name` 都填 = **双定位**（按 target 形态二选一，D8 兼容方案）；
        - 只有 `id_param` = **纯 id 定位**（target 必须是正整数，如 `task get 187`）；
        - 只有 `name_param` = **纯名定位**（target 原样作为名字，如 `role get tester`）。

    同名消歧不在本层做：`save_agent` / `get_agent_info` 各自的按名语义由工具层负责
    （`save_agent` 已下沉「同名多行 ⇒ ambiguous_target」的保护）。
    """

    id_param: Optional[str] = None
    name_param: Optional[str] = None

    @property
    def kind(self) -> str:
        if self.id_param and self.name_param:
            return "both"
        if self.id_param:
            return "id"
        if self.name_param:
            return "name"
        return "none"


def _loc(
    id_param: Optional[str] = None,
    name_param: Optional[str] = None,
) -> Locator:
    return Locator(id_param=id_param, name_param=name_param)


#: 选项别名 → 工具真名（#196 P2）。
#:
#: 为什么需要别名：真名是从 schema 派生的（`title_keyword` / `timezone` /
#: `clear_fields` / `keywords`），照抄到命令行上是**对模型最省心的形态**——schema 里
#: 写什么，命令行就写什么，不需要第二张映射表。但既有 help 与用例里已有短别名
#: （`--kw` / `--tz` / `--remove-field`），直接改名是破坏性变更且没带来收益。
#:
#: 所以「真名 + 别名」双轨：白名单同时认两者，**存储时保留用户写下的原名**
#: （`test_console_task` 对 `options["remove-field"]` 是按原名断言的），由
#: `normalize_option()` 只在需要落到工具参数时归一。
#:
#: 键是 `CommandSpec.key`（如 `"task ls"`），因为同名别名在不同命令下可能要落到
#: 不同真名（`name` 对 `role update` 是 `role_name` 的别名，对 `dept update` 就是真名）。
_OPTION_ALIASES: dict[str, dict[str, str]] = {
    "role ls": {"kw": "keywords"},
    "role update": {"role_name": "name"},
    "task ls": {"kw": "title_keyword"},
    "task update": {"remove-field": "clear_fields"},
    "time get": {"tz": "timezone"},
}


@dataclass(frozen=True)
class CommandSpec:
    """一条 console 子命令的静态定义。

    Attributes:
        resource: 资源词（dept / agent / room / role / time / help / 空串表示顶层命令）。
        action: 操作词（ls / get / create / update / rm / wake / dm / reload）。
        legacy_tool: 这条命令转发到的原工具名（#160）。它是**转发目标**，权限判定
            由统一入口在第 2 跳做，所以它必须非空；只有 `help` 这种纯本地命令可以留空。
        verb: 动作语义（见 `Verb`）。决定交给哪个通用处理器，也决定 help 里这一行
            归到哪一类。
        accepts_input: 是否接受配置输入（内联 JSON / `--from`）。配置输入始终可选：
            命令也可以只给 `--选项`；两者全无时由 SAVE 处理器按 create / update
            分别报 `missing_input_source` / `no_update_action`（#198）。
        needs_target: 是否需要位置参数 target。
        self_interrupt: 执行时会不会自中断当前 turn（#154 D6）。目前只有 `reload`。
        min_args / max_args: 位置参数个数约束。
        locator: 位置参数 target 落到哪个工具参数（见 `Locator`）。无 target 的命令留空。
        patch_by_name: 该命令的**语义就是更新**，所以无条件替调用方补
            `overwrite=True`（按 name 或按 id 定位都一样）。

            `save_dept` / `save_agent` / `save_room` 要求更新已有记录必须显式
            `overwrite=true`（对两种定位方式一视同仁，防止误覆盖）。`update` 命令
            不补这个参数就会在工具层被拒——**连自己指定的目标都改不了**。是否更新
            是**命令的语义**，不是要去查的事实，所以用命令表声明，而不是读库探测。

            `agent update` 也置真：同名多行由 `save_agent` 直接返回
            `ambiguous_target`（工具层保护）。
        input_defaults: 配置输入里**没出现**的字段用这里的值补上（只补「键不存在」，
            显式给 `null` 不覆盖）。

            这是**命令层契约**，不是从工具签名推出来的默认值，所以只能声明：
            - `dept create` 的 `member_names: []`：新建一个「只有负责人」的部门。
              工具签名里它是 `None`，而 `None` 在更新分支上有另一层含义（PATCH 保持
              现值）—— 两者不是同一件事，推不出来。旧实现靠手抄 `_pick(data,
              "member_names", [])` 表达，P2 收敛成纯转发后必须重新声明。

            `role create` 的 `type: "USER"` 已删除：`save_role_template` 现在自己
            把省略的 type 默认成 USER（PATCH 语义下沉后，默认值属于工具契约）。

            ⚠️ 只能挂在 create 上：`dept update` 若也补 `member_names`，一次只改
            responsibility 的 PATCH 会把成员清空。

    选项面（`options` / `param_names` / `boolean_options` / `option_kinds`）**不是字段**：
    它们从 `legacy_tool` 的 schema 派生（`consoleSchema`），访问时才 import。
    这样一来「加了参数但命令行不认」这种漂移在结构上就不可能发生。
    """

    resource: str
    action: str
    legacy_tool: str
    verb: Verb = "DIRECT"
    accepts_input: bool = False
    needs_target: bool = False
    self_interrupt: bool = False
    min_args: int = 0
    max_args: int = 0
    locator: Optional[Locator] = None
    patch_by_name: bool = False
    input_defaults: tuple[tuple[str, object], ...] = ()

    @property
    def key(self) -> str:
        return f"{self.resource} {self.action}".strip()

    # -- 选项面（延迟派生）---------------------------------------------------
    @property
    def alias_map(self) -> dict[str, str]:
        """本命令的「别名 → 真名」映射（见 `_OPTION_ALIASES`）。"""
        return _OPTION_ALIASES.get(self.key, {})

    @property
    def param_names(self) -> tuple[str, ...]:
        """底层工具的**真参数名**（schema 声明顺序）。无转发目标时为空。"""
        if not self.legacy_tool:
            return ()
        from .consoleSchema import param_schemas

        return tuple(param_schemas(self.legacy_tool))

    @property
    def options(self) -> tuple[str, ...]:
        """命令行认可的选项名（不带 `--`）：真名 + 该命令的别名。

        「未知选项」报错时给的就是这份清单，所以别名的存在必须在这里体现，否则
        用户照 help 写 `--kw` 会被自己的白名单拒绝。
        """
        real = self.param_names
        return real + tuple(alias for alias in self.alias_map if alias not in real)

    @property
    def params(self) -> tuple[str, ...]:
        """同 `options` 的别名：help 用法行只关心「这条命令有没有选项」。"""
        return self.options

    def normalize_option(self, name: str) -> str:
        """选项名 → 工具真名（别名归一；真名原样返回）。"""
        return self.alias_map.get(name, name)

    @property
    def boolean_options(self) -> frozenset[str]:
        """不带值即可出现的开关选项（schema 里 `type: boolean` 的那批，含别名形态）。

        P2 之前这是手写的 `frozenset({"recursive"})`：`dry_run` 被加进 schema 后它
        不会自动变成开关，解析器会把 `--dry-run` 的下一个 token 当成它的值吃掉。
        """
        if not self.legacy_tool:
            return frozenset()
        from .consoleSchema import boolean_params

        booleans = boolean_params(self.legacy_tool)
        aliases = {alias for alias, real in self.alias_map.items() if real in booleans}
        return frozenset(booleans | aliases)

    @property
    def option_kinds(self) -> dict[str, str]:
        """选项名（真名 + 别名）→ 类型类别（`integer` / `boolean` / `array` / ...）。"""
        if not self.legacy_tool:
            return {}
        from .consoleSchema import param_kinds

        kinds = dict(param_kinds(self.legacy_tool))
        kinds.update({alias: kinds.get(real, "string") for alias, real in self.alias_map.items()})
        return kinds

    @property
    def locator_kind(self) -> str:
        """定位形态：`both` / `id` / `name` / `none`（见 `Locator`）。"""
        return self.locator.kind if self.locator is not None else "none"


def _spec(
    resource: str,
    action: str,
    legacy_tool: str,
    *,
    verb: Verb,
    accepts_input: bool = False,
    needs_target: bool = False,
    self_interrupt: bool = False,
    min_args: int = 0,
    max_args: int = 0,
    locator: Optional[Locator] = None,
    patch_by_name: bool = False,
    input_defaults: tuple[tuple[str, object], ...] = (),
) -> CommandSpec:
    return CommandSpec(
        resource=resource,
        action=action,
        legacy_tool=legacy_tool,
        verb=verb,
        accepts_input=accepts_input,
        needs_target=needs_target,
        self_interrupt=self_interrupt,
        min_args=min_args,
        max_args=max_args,
        locator=locator,
        patch_by_name=patch_by_name,
        input_defaults=input_defaults,
    )


#: 全部 console 子命令。**这张表是权限校验、命令解析与 help 的唯一事实源。**
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    # 部门
    _spec("dept", "ls", "get_dept_info", verb="LIST"),
    _spec(
        "dept", "get", "get_dept_info", verb="GET",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc("dept_id", "name"),
    ),
    _spec(
        "dept", "create", "save_dept", verb="SAVE", accepts_input=True, max_args=0,
        # 旧实现 `member_names=_pick(data, "member_names", [])`：新建「只有负责人」的
        # 部门是合法用法（负责人会自动入成员）。见 `CommandSpec.input_defaults`。
        input_defaults=(("member_names", []),),
    ),
    _spec(
        "dept", "update", "save_dept", verb="SAVE",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        locator=_loc("dept_id", "name"),
        patch_by_name=True,
    ),
    _spec(
        "dept", "rm", "delete_dept", verb="DELETE",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc("dept_id", "name"),
    ),
    # 成员
    _spec("agent", "ls", "get_agent_info", verb="LIST"),
    _spec(
        "agent", "get", "get_agent_info", verb="GET",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc("agent_id", "name"),
    ),
    _spec("agent", "create", "save_agent", verb="SAVE", accepts_input=True),
    _spec(
        "agent", "update", "save_agent", verb="SAVE",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        # 同名消歧已下沉到 `save_agent`：按名定位且匹配多行时工具直接返回
        # ambiguous_target。console 只按 target 形态翻译成 name / agent_id。
        locator=_loc("agent_id", "name"),
        patch_by_name=True,
    ),
    # `wake` / `dm` 没有定位与补丁语义：位置参数原样就是工具参数（agent_name）。
    _spec("agent", "wake", "wake_up_agent", verb="DIRECT", needs_target=True, min_args=1, max_args=1),
    _spec("agent", "dm", "start_chat", verb="DIRECT", needs_target=True, min_args=1, max_args=1),
    # 房间
    _spec("room", "ls", "get_room_info", verb="LIST"),
    _spec(
        "room", "get", "get_room_info", verb="GET",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc("room_id", "name"),
    ),
    _spec("room", "create", "save_room", verb="SAVE", accepts_input=True),
    _spec(
        "room", "update", "save_room", verb="SAVE",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        locator=_loc("room_id", "name"),
        patch_by_name=True,
    ),
    _spec(
        "room", "rm", "delete_room", verb="DELETE",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc("room_id", "name"),
    ),
    # 角色模板
    _spec("role", "ls", "list_role_templates", verb="LIST"),
    _spec(
        "role", "get", "get_role_template", verb="GET",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc(name_param="role_name"),
    ),
    _spec("role", "create", "save_role_template", verb="SAVE", accepts_input=True),
    _spec(
        "role", "update", "save_role_template", verb="SAVE",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        # `save_role_template` 的定位参数真名叫 `name`；对外统一叫 `role_name`
        # （别名见 `_OPTION_ALIASES`）。PATCH 语义已下沉到工具：省略 type/soul/i18n
        # = 保持现值，所以这里只需按更新语义补 overwrite。
        locator=_loc(name_param="name"),
        patch_by_name=True,
    ),
    _spec(
        "role", "rm", "delete_role_template", verb="DELETE",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc(name_param="role_name"),
    ),
    # 任务（#187）
    #
    # `legacy_tool` 照旧填转发目标，第 2 跳做权限校验与执行。task 三工具在主干上
    # 都已是 `ToolCategory.BASIC`（见 `toolRegistry.CATEGORY_CONFIG`），所以这三条命令
    # 的可达性由第 2 跳的原工具权限决定，console 侧不另算。
    #
    # 选项面（`status` / `assignee` / `reviewer` / `scope` / `kw` / `limit`，以及 P2
    # 起自动派生出来的 `open_only` / `event_limit`）全部来自 `list_tasks` /
    # `get_task_detail` 的 schema，本表不再手抄。
    _spec("task", "ls", "list_tasks", verb="LIST"),
    _spec(
        "task", "get", "get_task_detail", verb="GET",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc(id_param="task_id"),
    ),
    _spec(
        "task", "update", "update_task", verb="SAVE",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        # 任务 id 是必填位置参数；配置输入与 `--remove-field` 都可省略 —— 单独给
        # `task update 187 --remove-field reviewer_id` 是合法调用（撤销验收人）。
        locator=_loc(id_param="task_id"),
    ),
    # 其他
    _spec("time", "get", "get_time", verb="GET"),
    _spec("reload", "", "reload_team", verb="DIRECT", self_interrupt=True),
)

#: `help` 是纯本地命令，不映射任何原工具，也不做权限校验（它不碰数据）。
HELP_SPEC = _spec("help", "", "", verb="DIRECT")

COMMAND_INDEX: dict[str, CommandSpec] = {spec.key: spec for spec in COMMAND_SPECS}

#: 同一资源下可用的操作，用于报错时给候选。
ACTIONS_BY_RESOURCE: dict[str, tuple[str, ...]] = {}
for _spec_item in COMMAND_SPECS:
    _existing = ACTIONS_BY_RESOURCE.get(_spec_item.resource, ())
    ACTIONS_BY_RESOURCE[_spec_item.resource] = _existing + (_spec_item.action,) if _spec_item.action else _existing

#: 同一资源下的完整 spec（help 自动生成按资源分组时用，保持命令表顺序）。
SPECS_BY_RESOURCE: dict[str, tuple[CommandSpec, ...]] = {}
for _spec_item in COMMAND_SPECS:
    SPECS_BY_RESOURCE[_spec_item.resource] = SPECS_BY_RESOURCE.get(_spec_item.resource, ()) + (_spec_item,)

RESOURCE_NAMES: tuple[str, ...] = tuple(ACTIONS_BY_RESOURCE.keys())

#: 顶层命令（没有资源词）。
TOP_LEVEL_COMMANDS: tuple[str, ...] = ("help", "reload")

#: `--from` 支持的来源前缀。v1 只放开 `payload`（#154 D1），其余**显式拒绝**。
SUPPORTED_FROM_SOURCES: tuple[str, ...] = ("payload",)


def resolve_command_spec(tokens: list[str]) -> tuple[CommandSpec | None, str | None]:
    """从 token 列表里认出命令，返回 (spec, 错误信息)。

    `help` 单独处理：`help`、`help dept`、`help dept create` 都合法，
    且不参与 `COMMAND_INDEX` 的两段式查找。
    """
    if not tokens:
        return None, "命令为空"

    head = tokens[0].lower()

    if head in ("help", "?"):
        return HELP_SPEC, None

    if head == "reload":
        # `reload` 不接受任何位置参数（也不接受选项，见 #165 删 confirm 闸门）。
        # 多余 token 的合法性由后续 `_parse_options` / `_check_arity` 报错。
        return COMMAND_INDEX["reload"], None

    spec = COMMAND_INDEX.get(f"{head} {tokens[1].lower()}" if len(tokens) > 1 else "")
    if spec is not None:
        return spec, None

    if head not in ACTIONS_BY_RESOURCE:
        return None, (
            f"未知资源：'{head}'。可用资源：{' | '.join(RESOURCE_NAMES)}"
        )
    return None, (
        f"未知命令：'{head} {tokens[1] if len(tokens) > 1 else ''}'。"
        f"可用操作：{' | '.join(ACTIONS_BY_RESOURCE[head])}"
    )


def command_console_visible(spec: CommandSpec) -> bool:
    """该命令当前是否可通过 console 使用（由转发目标的可见性决定）。

    运行时判定，不在 import 期固化：D2 开关可能在同一进程内被切换（测试就依赖
    这一点）。延迟 import `core` 避免 `consoleCommands` → `core` → `consoleTool` →
    `consoleCommands` 的模块级循环。
    """
    if not spec.legacy_tool:
        return True
    from constants import ToolVisibility

    from .core import get_func_tool, resolve_effective_visibility

    func_tool = get_func_tool(spec.legacy_tool)
    # 工具尚未 `load_func_tools()`（部分单测直接调 console / 判定器）时按命令表声明
    # 的基准 BOTH 判定：不能因为「还没加载」把 reload 之类的命令误判成不可用。
    declared = func_tool.visibility if func_tool is not None else ToolVisibility.BOTH
    return resolve_effective_visibility(spec.legacy_tool, declared).console_visible
