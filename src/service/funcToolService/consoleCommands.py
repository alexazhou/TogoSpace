"""`console` 的静态常量：kebab 换算规则与 `--from` 来源白名单，只依赖标准库。

换算规则和来源白名单各抄一份到解析层、注册表，改一处另一处会悄悄不一致。`to_kebab` 是命令行选项形态唯一的换算入口（`consoleParsing` / `consoleTool` / `consoleRegistry` 三处都从这里取），`SUPPORTED_FROM_SOURCES` 决定 `--from` 认哪些来源；两者都不 import 任何 service 模块，所以本模块的单元测试不用先起数据库。

新增命令不要动本文件：给工具挂 `@tool(console=ConsoleCommand(...))`、别名写成 `Arg.alias`。要放开来源前缀改 `SUPPORTED_FROM_SOURCES`。旧手抄表是被设计 §6 P1 删掉的，别恢复。
"""

from __future__ import annotations

#: 选项别名与 kebab 写法：命令行既认真名（schema 里的下划线式）也认短别名，展示统一用 kebab。
#:
#: schema 真名不能动（上游工具参数键和 mcp2cli 都不碰），所以只在解析层做两件事：`Arg.alias` 声明别名、`ResolvedCommand.normalize_option()` 落到工具参数时换回真名，保存时保留用户写下的原名。直接改掉短别名（`--kw` / `--tz` / `--remove-field`）会打断既有 help 和用例；只认真名，命令行上又得多维护一张映射表。
#: `--dry_run` 与 `--dry-run` 现在都能解析、help 只显示 kebab。`command_options_map` 里「真名 → 真名」那条兼容映射的删除门槛（0.6.0）已经过了但还没删；删它是独立改动，删前先确认 `test_console_*` 里没有按旧下划线写法断言的用例。
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
