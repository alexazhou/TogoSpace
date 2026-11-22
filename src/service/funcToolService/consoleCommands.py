"""console 的静态常量：kebab 归一规则与 `--from` 来源白名单。

P0（#315）起命令表**不再是手抄的**：命令由工具上的 `@tool(console=ConsoleCommand(...))`
声明、由 `consoleRegistry` 惰性派生（`get_table()`）。原来那张手抄表连同 `Locator` /
`CommandSpec` / `_OPTION_ALIASES` / `HELP_SPEC` / `COMMAND_INDEX` /
`resolve_command_spec` / `command_console_visible` 一起删除（设计 §6 P1），本模块只剩
两个仍被别处引用的静态事实：

1. `to_kebab`：命令行选项形态的**唯一换算规则** —— `consoleParsing` / `consoleTool` /
   `consoleRegistry` 三处都从这里取（`consoleSchema._kebab` 是同规则副本，理由见那边
   注释）。
2. `SUPPORTED_FROM_SOURCES`：`--from` 支持的来源前缀。v1 只放开 `payload`（#154 D1），
   其余**显式拒绝**。

**新增命令不要改本文件**：给工具挂 `@tool(console=ConsoleCommand(...))` 即可，选项面由
签名派生、别名挂在 `Arg.alias` 上（见 `toolSpec`）。本模块顶层只依赖标准库，因此
`import consoleCommands` 不拉起任何 service 模块，单元测试仍可脱离 DB 直接跑。

为什么不把这两个常量和解析层合并：`consoleRegistry` 顶层就要 `to_kebab`
（`build()` 校验别名 kebab 后撞键），搬进 `consoleParsing` 会让「注册表 → 解析层」多一条
依赖边；留在零依赖的静态模块里，包内 import 方向最简单。
"""

from __future__ import annotations

#: 选项别名与 kebab 归一（#196 P2；别名的落点 P0 #315 起改过一次）。
#:
#: 为什么需要别名：真名是从 schema 派生的（`title_keyword` / `timezone` /
#: `clear_fields` / `keywords`），照抄到命令行上是**对模型最省心的形态**——schema 里
#: 写什么，命令行就写什么，不需要第二张映射表。但既有 help 与用例里已有短别名
#: （`--kw` / `--tz` / `--remove-field`），直接改名是破坏性变更且没带来收益。
#:
#: 所以「真名 + 别名」双轨：命令的选项集合同时认两者，**存储时保留用户写下的原名**
#: （`test_console_task` 对 `options["remove-field"]` 是按原名断言的），由
#: `ResolvedCommand.normalize_option()` 只在需要落到工具参数时归一。
#: 别名的**声明落点**从 P0 起不再是按命令 key 索引的 `_OPTION_ALIASES` 表，而是跟着
#: 参数声明走的 `Arg.alias` —— `consoleRegistry` 由它派生「别名 → 真名」映射。
#:
#: **kebab-case（#220 A2，任务单 #221）**：命令行选项的展示形态统一 kebab
#: （`--dry-run` / `--remove-field` / `--clear-fields`），schema 真名（下划线式，
#: 如 `dry_run`）保持不动 —— 上游工具参数键与 mcp2cli 上游都不碰，只改 console
#: 的命令行形态与 help 展示。
#:
#: **兼容期与移除触发条件**（清单 §A2 建议：旧名保留整整一个版本）：旧下划线名
#: 在解析层继续被接受（`--dry_run` ≡ `--dry-run`），但 help 只展示 kebab 形式。
#: 移除触发：当 `src/version.py` 的 `__version__` 进入 **0.6.0 及以后**（本条最初按
#: 0.5.2-beta1 写，现值已是 **0.5.2-beta5**，仍未进入 0.6.0），即从
#: `ResolvedCommand.command_options_map` 里删掉「真名 → 真名」这条兼容映射 ——
#: 那时 `--dry_run` 会报 unknown_option，属预期行为。写在这里而不是 TODO：版本提升的
#: 人一定会看到这条。
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


#: `--from` 支持的来源前缀。v1 只放开 `payload`（#154 D1），其余**显式拒绝**。
SUPPORTED_FROM_SOURCES: tuple[str, ...] = ("payload",)
