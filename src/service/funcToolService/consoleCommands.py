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
#: **kebab-case（#220 A2，任务单 #221）**：命令行选项的展示形态统一 kebab
#: （`--dry-run` / `--remove-field` / `--clear-fields`），schema 真名（下划线式，
#: 如 `dry_run`）保持不动 —— 上游工具参数键与 mcp2cli 上游都不碰，只改 console
#: 的命令行形态与 help 展示。
#:
#: **兼容期与移除触发条件**（清单 §A2 建议：旧名保留整整一个版本）：旧下划线名
#: 在解析层继续被接受（`--dry_run` ≡ `--dry-run`），但 help 只展示 kebab 形式。
#: 移除触发：当 `src/version.py` 版本号进入 **0.6.0 及以后**（当前 0.5.2-beta1），
#: 即从 `command_options_map` 里删掉「真名 → 真名」这条兼容映射、并同步删掉
#: 本文件「旧别名形态」的兼容注册 —— 那时 `--dry_run` 会报 unknown_option，
#: 属预期行为。写在这里而不是 TODO：版本提升的人一定能看到这条。
def to_kebab(name: str) -> str:
    """schema 真名（下划线式）→ 命令行选项名（kebab 式）。幂等、无下划线时原样。

    防御：转换结果出现连续 `-` 或首尾 `-`（如 `a__b` → `a--b`）说明真名里藏着
    非常规命名 —— 在**派生时**炸掉，而不是让运行时报错文案显示一个打不出来的
    选项名（M2 的「转换错位」在这条被咬）。
    """
    kebab = name.replace("_", "-")
    if "--" in kebab or kebab.startswith("-") or kebab.endswith("-"):
        raise ValueError(f"kebab 转换产生非法选项形态：{name!r} -> {kebab!r}")
    return kebab
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

        filter_options: 该命令的**过滤项**（#222 A3）。原工具（`list_tasks`）要求这些
            参数至少给一个，否则返回 `missing_target`；命令层据此判断要不要自动补
            `fallback_options`，也据此在 help 里说清「不给就会被补成什么」。
        fallback_options: 过滤项**全缺**时自动补的选项（#222 A3）。补了必须显式告知
            （返回体 `auto_filled_filters` + message），不许静默改口径——「以为查的是
            全团队、其实只查了自己」比直接报错更贵。只补「键不存在」，显式给了不覆盖。
        empty_target_hint: 缺位置参数时的说明块（#222 A4）。有它时 arity 失败改报
            `missing_target` 并把这几个 kind 说明与用法示例一并给出，取代干巴巴的
            「需要 1~1 个位置参数，收到 0 个」。
        success_hint: 成功返回体要追加的一句提醒（#222 A6）。用于「命令做成了、但还有
            一步必须人做」的场合（建单后要另发消息通知执行人），免得模型以为发完就算了。
            走 `post_process` 追加，**不改原工具的 message**（服务层语义不动）。

        example: 该命令的**正确调用示例**（#281 P1，固定文案 + `<占位符>`，不做动态拼装）。
            未知选项 / 「位置参数被写成选项」这类报错里直接给这一行，取代「可用选项」清单：
            使用者复制即可执行，不必照着词汇表自己再拼一遍。
            **只有确实有示例可给的命令才填**（目前是 `mcp call`）——没填的命令报错只给
            「未知选项：--x。」，绝不借用别的命令的文案（`task ls --bogus` 弹 MCP 的
            「参数请放进 --args」是笑话，参见 #281 单的污染判据）。

        option_tip: 该命令未知选项时追加的一句**领域说明**（#281 P1）。与 `example` 配套：
            `mcp call` 用它说「MCP 工具的参数请放进 --args」。空串 = 不加。

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
    filter_options: tuple[str, ...] = ()
    fallback_options: tuple[tuple[str, object], ...] = ()
    empty_target_hint: tuple[str, ...] = ()
    success_hint: str = ""

    example: str = ""
    option_tip: str = ""

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
    def command_options_map(self) -> dict[str, str]:
        """命令行选项形态 → 工具真名（#221 kebab 的唯一归一表）。

        键的三类来源（**一个真名允许多个命令行形态**，这是兼容期的本义）：
        - kebab 形态（`--clear-fields` → `clear_fields`）：help 展示的**主形态**；
        - 真名形态（`--clear_fields` → `clear_fields`）：**兼容期别名**（见
          `to_kebab` 处的移除触发条件）；
        - 手写短别名（`--kw` → `title_keyword`、`--remove-field` → `clear_fields`），
          旧别名含下划线的（`--role_name`）同时注册 kebab 形态 `--role-name`。

        冲突即炸（派生时 ValueError）：两个真名 kebab 后撞键、或别名撞真名，
        都说明命名出问题了，静默选边只会让其中一种写法悄悄失效。
        """
        mapping: dict[str, str] = {}

        def _register(form: str, real: str) -> None:
            existing = mapping.get(form)
            if existing is not None and existing != real:
                raise ValueError(
                    f"选项形态 {form!r} 冲突：{existing!r} vs {real!r}（命令 {self.key}）"
                )
            mapping[form] = real

        for real in self.param_names:
            _register(to_kebab(real), real)
            _register(real, real)  # 兼容期：旧下划线名（0.6.0 移除，见 to_kebab 注释）
        for alias, real in self.alias_map.items():
            _register(alias, real)
            if "_" in alias:
                _register(to_kebab(alias), real)
            if "-" in alias:
                # 短别名的「下划线直译」形态（--remove_field → remove-field）也放行：
                # 手写别名与真名之间的形态换算用户记不准，宽容收下、归一同一真名，
                # 报错与 help 只展示 kebab 形态。
                _register(alias.replace("-", "_"), real)
        return mapping

    @property
    def display_options(self) -> tuple[str, ...]:
        """选项名的**展示清单**（kebab 形态 + 手写别名），不含兼容旧名、**不含位置参数**。

        「未知选项」报错的可用清单、help 相关推导都从这份走 —— 旧名只是解析层
        悄悄接受，不该在展示面露脸把用户往旧写法引（需求 3：help 只展示 kebab）。

        #281 P1：**位置参数承载的那个工具参数必须排除**。`target` / `name` 在
        `mcp call` / `mcp rm` 等命令里是位置参数，但因为选项面是从工具签名派生的，
        它们也会出现在这份清单里 —— 把 `--target` 摆在「可用选项」里，等于主动
        教人写错形态（#281 P1/P2 的现场就是这么来的）。
        """
        positional = set(self.positional_param_names)
        return tuple(
            form
            for form, real in self.command_options_map.items()
            if "_" not in form and real not in positional
        )

    @property
    def positional_param_names(self) -> tuple[str, ...]:
        """位置参数承载的**工具参数真名**（来自 `locator` 的声明）。

        `mcp call` ⇒ `("target",)`；`mcp rm` / `dept get` ⇒ `("name",)`；无 locator ⇒ `()`。
        只用于把「位置参数」和「真选项」区分开，不参与解析判定（判定仍走 `options`，
        兼容期旧名照旧放行）。
        """
        if self.locator is None:
            return ()
        return tuple(
            param for param in (self.locator.id_param, self.locator.name_param) if param
        )

    @property
    def options(self) -> tuple[str, ...]:
        """命令行认可的选项名（不带 `--`）：kebab + 兼容旧名 + 该命令的别名。

        「未知选项」的**判定**用这份（兼容期内旧名必须放行），报错文案里的
        可用清单用 `display_options`（只展示 kebab）—— 判定与展示分开是刻意的：
        白名单若只列 kebab，兼容期里照旧写法敲命令会被自己的白名单拒绝。
        """
        return tuple(self.command_options_map)

    @property
    def params(self) -> tuple[str, ...]:
        """同 `options` 的别名：help 用法行只关心「这条命令有没有选项」。"""
        return self.options

    def normalize_option(self, name: str) -> str:
        """命令行选项形态 → 工具真名（kebab / 兼容旧名 / 短别名一律归一）。"""
        return self.command_options_map.get(name, name)

    @property
    def boolean_options(self) -> frozenset[str]:
        """不带值即可出现的开关选项（schema 里 `type: boolean` 的那批，含全部命令行形态）。

        P2 之前这是手写的 `frozenset({"recursive"})`：`dry_run` 被加进 schema 后它
        不会自动变成开关，解析器会把 `--dry-run` 的下一个 token 当成它的值吃掉。

        kebab 化（#221）后真名/kebab/别名三种形态都要在集合里：解析层拿**用户写下
        的形态**来判布尔，`--dry-run`（kebab）必须命中，否则它会被当成带值选项。
        """
        if not self.legacy_tool:
            return frozenset()
        from .consoleSchema import boolean_params

        booleans = boolean_params(self.legacy_tool)
        result: set[str] = set()
        for real in booleans:
            result.add(real)            # 真名形态（兼容期）
            result.add(to_kebab(real))  # kebab 形态
        for alias, real in self.alias_map.items():
            if real in booleans:
                result.add(alias)
                if "_" in alias:
                    result.add(to_kebab(alias))
        return frozenset(result)

    @property
    def option_kinds(self) -> dict[str, str]:
        """选项形态（kebab / 兼容旧名 / 短别名）→ 类型类别。

        与 `boolean_options` 同理：解析层 `_store` 按**用户写下的形态**查 kind，
        kebab 形态必须命中，否则会静默退化为 `string`（类型校验整条失效）。
        """
        if not self.legacy_tool:
            return {}
        from .consoleSchema import param_kinds

        kinds = dict(param_kinds(self.legacy_tool))
        for form, real in self.command_options_map.items():
            kinds.setdefault(form, kinds.get(real, "string"))
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
    filter_options: tuple[str, ...] = (),
    fallback_options: tuple[tuple[str, object], ...] = (),
    empty_target_hint: tuple[str, ...] = (),
    success_hint: str = "",
    example: str = "",
    option_tip: str = "",
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
        filter_options=filter_options,
        fallback_options=fallback_options,
        empty_target_hint=empty_target_hint,
        success_hint=success_hint,
        example=example,
        option_tip=option_tip,
    )


#: `mcp` 命令缺 target 时的说明块（#222 A4）。
#:
#: 放在命令表**外面**：元组字面量里不能赋值，而这段文案两条 mcp 命令共用一份，
#: 各抄一遍就会漂移（`ls` 说 openapi 默认、`call` 说 mcp 默认，模型照谁写都不对）。
MCP_KIND_HINT: tuple[str, ...] = (
    "用法：mcp <ls|call> <target> [--kind mcp|openapi|graphql] [--protocol stdio|sse|streamable_http]，target 形态取决于 kind：",
    "  --kind mcp（默认）      MCP server；配合 --protocol stdio（启动命令）/ sse / streamable_http（URL，缺省）",
    "  --kind openapi          spec 文件路径或 URL",
    "  --kind graphql          GraphQL endpoint",
    "示例：mcp ls ./openapi.json ｜ mcp ls http://127.0.0.1:8000/mcp --kind mcp ｜ mcp ls \"npx -y server\" --kind mcp --protocol stdio",
)


#: `mcp call` 的**正确调用示例**（#281 P1）：固定文案 + `<占位符>`，不做动态拼装
#: （Operator 02:10/02:12 明确否掉拼装：要处理值是否存在、引号怎么写，脆且必须多一层
#: 分支）。P1（未知选项）与 P2（`--target` 被写成选项）**共用这一处实现**
#: （Operator 02:13 定）：示例行只有一份，避免两份文案各自漂移。
MCP_CALL_EXAMPLE: str = "mcp call <名称> --tool <工具名> --args '{\"键\":\"值\"}'"

#: `mcp call` 未知选项时的一句领域说明（#281 P1）。**只挂 mcp 命令** ——
#: `unknown_option` 是所有 console 命令共用的，把 MCP 专属文案全局套上会让
#: `task ls --bogus` 弹出「MCP 工具的参数请放进 --args」（#281 单里专门有这条
#: 「非 mcp 命令不被污染」判据）。
MCP_OPTION_TIP: str = "MCP 工具的参数请放进 --args。"


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
    #
    # `filter_options` + `fallback_options`（#222 A3）：`list_tasks` 要求至少给一个过滤
    # 项，裸给 `task ls --status TODO` 会被原工具以 `missing_target` 拒。命令层补
    # `scope=self`（「我的待办」），因为这才是命令行上最常用、也最可控的列表口径；
    # 补完在返回体 `auto_filled_filters` 与 message 里显式说明（禁止静默）。
    _spec(
        "task", "ls", "list_tasks", verb="LIST",
        filter_options=("assignee", "reviewer", "scope", "title_keyword"),
        fallback_options=(("scope", "self"),),
    ),
    # `task create`（#222 A6）：转发 `create_task`，与其它 create 一样接受内联 JSON /
    # `--from payload` / 纯 --选项 三种输入。`success_hint` 是命令级事实：建单**不会**
    # 唤醒执行人，必须另发消息通知——原工具只在 docstring 里写了这句，返回体里没有，
    # 于是「建完就等」成了高频误用。提示走 post_process 追加，不改原工具返回体。
    _spec(
        "task", "create", "create_task", verb="SAVE", accepts_input=True, max_args=0,
        success_hint="建单不会自动唤醒执行人：请另发 send_chat_msg 通知对方（本命令不会替你通知）。",
    ),
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
    # 容器（#203：只开 note 一条，其余容器命令不进 console）
    #
    # 两个位置参数：`container note <id> <text>`。位置参数按 `update_container_note`
    # 的 schema 声明顺序映射（`container_id` → `note`），所以工具签名必须把
    # `record_id` 排在 `note` 之后（见 `tools.update_container_note`）。
    #
    # 「清空」只认显式空串（`container note <id> ""`）：少给文本会被 arity 拒，
    # **不允许静默清空** —— 漏写一个参数就把说明擦掉，代价远大于多打一对引号。
    _spec(
        "container", "note", "update_container_note", verb="DIRECT",
        min_args=2, max_args=2,
    ),
    # 其他
    _spec("time", "get", "get_time", verb="GET"),
    _spec("reload", "", "reload_team", verb="DIRECT", self_interrupt=True),
    # 外部服务（#202 mcp2cli 接入）：一条 `mcp` 资源词转发到 `mcp_call`。
    #
    # 位置参数只承载 target（`mcp list ./openapi.json`）；`tool` / `args` 走选项，
    # 因为现有 DIRECT 处理器只把**第一个**位置参数映射到第一个工具参数，要多段位置
    # 参数就得改 console 核心语义（#202 明确不改既有语义）。选项面由 `mcp_call` 的
    # signature 派生（`--tool` / `--args '{"k":"v"}'` / `--kind` / `--extra`），
    # `args` 声明成 dict ⇒ 上游 `_convert_option` 按 object 走 JSON 解析。
    #
    # `mcp call` 用 SAVE 只为拿到 `input_defaults`（把 `action` 补成 `call`）；
    # 它不落库、不吃 `overwrite`，`patch_by_name` 保持默认。
    # `empty_target_hint`（#222 A4）：裸敲 `mcp ls` 只会拿到「需要 1~1 个位置参数，
    # 收到 0 个」——而 target 的形态完全取决于 `--kind`，不说清就没法照着改。这里把
    # kind 与 target 的对应关系、两个可抄的示例直接放进失败返回体。
    _spec(
        "mcp", "call", "mcp_call", verb="SAVE",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        locator=_loc(name_param="target"),
        input_defaults=(("action", "call"),),
        empty_target_hint=MCP_KIND_HINT,
        example=MCP_CALL_EXAMPLE,
        option_tip=MCP_OPTION_TIP,
    ),
    # MCP 注册表（#224，按 #223 设计稿 §3.5）：
    #
    # `mcp call <name>` 与 `mcp call <完整 target>` **无法靠 spec 区分**（一个 spec
    # 只有一个 legacy_tool），name 解析只能放在工具内部（`tools.mcp_call` 入口的
    # `resolve_name`，沿 #223 §3.6）——本表对 `mcp call` 一个字符都不改。
    #
    # `add`：照抄 dept create（create 族）的 SAVE 形态。动作词用 `add` 是**有意
    # 偏离**（§3.2.1 已裁定）：create 建的是 team 内新实体，add 登记的是指向外部
    # 服务的指针；差异已钉进 `_HELP_OVERLAY`（「登记到 team 级注册表，非创建业务
    # 实体」）。真正拦住多余位置参数的是 accepts_input 分支写死的 upper=1，不是
    # `max_args=0`（那只是与 dept create 的写法同构，§3.2/C1）。
    _spec(
        "mcp", "add", "save_mcp_server", verb="SAVE",
        accepts_input=True, max_args=0,
    ),
    # `rm`：照抄 dept rm / room rm 的 DELETE 形态；只按 name 精确删，不存在时工具层
    # 返回 mcp_name_not_found（不静默成功，沿 §5.2）。
    _spec(
        "mcp", "rm", "delete_mcp_server", verb="DELETE",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc(name_param="name"),
    ),
    # `enable` / `disable`（#273）：同一底层工具 set_mcp_server_enabled，用
    # input_defaults 把 enable 补成 true/false；禁用≠删除，`mcp ls` 仍可见。
    _spec(
        "mcp", "enable", "set_mcp_server_enabled", verb="SAVE",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc(name_param="name"),
        input_defaults=(("enable", True),),
    ),
    _spec(
        "mcp", "disable", "set_mcp_server_enabled", verb="SAVE",
        needs_target=True, min_args=1, max_args=1,
        locator=_loc(name_param="name"),
        input_defaults=(("enable", False),),
    ),
    # `ls`：三条里唯一无法同构的一条（设计稿 §3.3）。需求要求「无参列注册表、
    # 有参列工具清单」两种形态都到达工具——LIST 丢位置参数、GET 强制 target、
    # SAVE 的 arity 写死 0~1/1~2，只有 DIRECT 支持 0~1 段位置参数。**这是本设计
    # 唯一一处新增的机制用法**，三条硬事实见设计稿 §3.3。有参分支由工具委托
    # `mcp_call`（action=list），name 解析复用同一条 `resolve_name`（C3）。
    _spec(
        "mcp", "ls", "list_mcp_servers", verb="DIRECT",
        min_args=0, max_args=1,
    ),
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
