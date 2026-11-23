"""help 与命令表的**派生一致性**护栏（P0 重写：命令表改由 `@tool(...)` 派生）。

旧版拿 `consoleCommands.COMMAND_SPECS` 当「命令表」，逐条比对 help；P0 之后那张表
只剩 P1 待删的残留，真正的运行时命令表是 `consoleRegistry.get_table()`。本文件因此
全部改为对着 **registry** 比对——这恰好也是设计 §5.3「help 纯拼接」的可观测结果：

- 键集合：`table.specs` ↔ `HELP_DETAIL` 双向全等；
- 参数面：help 里列的 `--参数` ↔ 工具 schema properties（kebab 形态）；
- 摘要：`HELP_SUMMARY` 的资源行 ↔ `table.by_resource`（预设资源/动作顺序）；
- 渲染：schema 里每个参数都必须能被 `describe_param` 渲染出来。
"""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any

import pytest

from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleRegistry
from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct
from service.funcToolService import core as funcCore
from service.funcToolService import tools as funcTools
from service.roomService import ToolCallContext

TABLE = consoleRegistry.get_table()


# --------------------------------------------------------------------------- #
# 护栏 1~3：help 与命令表的派生一致性
# --------------------------------------------------------------------------- #


def test_help_keys_match_command_table() -> None:
    """help 的键集合必须与命令表**完全相同**（双向）。

    单向比较会漏掉一半：只查「命令表 ⊆ help」时，删掉一条命令但 help 里留着旧行不红；
    只查「help ⊆ 命令表」时，加了命令不写 help 也不红。
    """
    spec_keys = {spec.key for spec in TABLE.specs}
    help_keys = set(ct.HELP_DETAIL)

    assert spec_keys == help_keys, (
        f"命令表与 help 不一致：\n"
        f"  只在命令表（help 缺）：{sorted(spec_keys - help_keys)}\n"
        f"  只在 help（命令表已删）：{sorted(help_keys - spec_keys)}"
    )


def test_help_lists_exactly_the_schema_params() -> None:
    """help 的参数面必须与命令表**双向**对齐（方案 B / #323 之后的三个分段口径）。

    这是「参数面从声明派生」的可观测结果：派生逻辑回退成手抄、或 schema/命令表加了
    参数但渲染漏了，这里立刻红。三个方向分别钉：

    1. **选项**：help 里 `  --x` 行 == `display_options` 的主形态（别名内联渲染，不成行）；
    2. **位置参数**：每个位置槽在「位置参数：」段里有 `  <token>` 行，token 取自签名。

    （`container note` 的 `record_id` 这类「命令行上不可用」的参数是否该从 help 隐去，
    等 Operator 拍板声明层口径，本单只留报告条目、不动渲染。）
    """
    problems: list[str] = []

    for cmd in TABLE.specs:
        if cmd.tool is None:
            continue
        detail = ct.HELP_DETAIL[cmd.key]
        listed_options = set(re.findall(r"^  --([A-Za-z][\w-]*)", detail, flags=re.MULTILINE))

        # ① 选项面（双向）：help 里不许出现白名单外的选项；白名单里的**主形态**必须都在。
        #    例外是口径不是遗漏：别名（`--kw` / `--status` / `--remove-field` …）内联渲染
        #    在真名行里（「（别名 --kw）」），不会自成一行。
        alias_forms = {cc.to_kebab(alias) for alias in cmd.alias_map}
        must_list = set(cmd.display_options) - alias_forms
        if listed_options - set(cmd.display_options):
            problems.append(f"{cmd.key}: help 列了但选项白名单没有 {sorted(listed_options - set(cmd.display_options))}")
        if must_list - listed_options:
            problems.append(f"{cmd.key}: 选项白名单有但 help 没列 {sorted(must_list - listed_options)}")

        # ② 位置面带：每个位置槽一行 `  <token>`，且 token 与签名同源
        section = detail.split("位置参数：", 1)[1] if "位置参数：" in detail else ""
        for arg in cmd.positional_args:
            token = ct._positional_tokens(cmd, arg)[1]
            if f"  {token} " not in section:
                problems.append(f"{cmd.key}: 位置段缺 {token} 行（位置槽 {arg.target.name}）")

    assert not problems, "help 与 schema 参数面不一致：\n" + "\n".join(problems)


def _section_rows(detail: str, title: str) -> list[str]:
    """取某段标题下的行（缩进两格起，遇到第一个非缩进行即段尾）。"""
    if title not in detail:
        return []
    rows: list[str] = []
    for line in detail.split(title, 1)[1].splitlines():
        if not rows and not line.strip():
            continue  # 标题行本身在 split 后留下一个空串
        if line.startswith("  "):
            rows.append(line)
        else:
            break
    return rows


def test_help_sections_are_never_empty() -> None:
    """交付标准（#323）：「无对应内容时不留空标题」。

    `位置参数：` / `选项：` 只在下面**真有行**时才许出现；反向也钉：真有位置槽就有
    「位置参数：」段（否则参数被静默吞掉）。
    """
    problems: list[str] = []
    for cmd in TABLE.specs:
        detail = ct.HELP_DETAIL[cmd.key]
        for title in ("位置参数：", "选项："):
            if title in detail and not _section_rows(detail, title):
                problems.append(f"{cmd.key}: 「{title}」段是空的")
        if cmd.positional_args and "位置参数：" not in detail:
            problems.append(f"{cmd.key}: 有位置槽却没有「位置参数：」段")
        if "位置参数：" in detail and not cmd.positional_args:
            problems.append(f"{cmd.key}: 没有位置槽却出现了「位置参数：」段")
    assert not problems, "help 分段有空标题：\n" + "\n".join(problems)


def test_positional_required_marker_follows_arg_declaration() -> None:
    """位置行必填标记 == **声明** `Arg.required`（不拿 schema required 比 —— 同源无鉴别力）。

    PM 的验收判据：造「声明必填 / schema 可选」的现场，本用例必须在**每一个**这类
    槽位上都红（`dept get.dept_id`、`agent get.agent_id`、`room get.room_id`、
    `container note.container_id`、`container note.note`），而不是只红一条。
    """
    problems: list[str] = []
    checked = 0
    for cmd in TABLE.specs:
        rows = _section_rows(ct.HELP_DETAIL[cmd.key], "位置参数：")
        for arg in cmd.positional_args:
            token = ct._positional_tokens(cmd, arg)[1]
            line = next((row for row in rows if row.startswith(f"  {token} ")), None)
            if line is None:  # 缺行由 test_help_lists_exactly_the_schema_params 负责
                continue
            checked += 1
            want = "（必填）" if arg.required else "（可选）"
            other = "（可选）" if arg.required else "（必填）"
            if want not in line or other in line:
                problems.append(
                    f"{cmd.key}.{arg.target.name}: 声明 required={arg.required}，"
                    f"help 行却是 {line!r}"
                )
    assert checked >= 17, f"位置槽只核到 {checked} 个，覆盖面不对"
    assert not problems, "位置行必填标记与声明不一致：\n" + "\n".join(problems)


def test_help_summary_is_derived_from_command_table() -> None:
    """`HELP_SUMMARY` 的资源与操作 == `table.by_resource`（规范排序后）派生结果。"""
    summary = ct.HELP_SUMMARY
    for resource, commands in TABLE.by_resource.items():
        assert resource in summary, f"HELP_SUMMARY 缺少资源 {resource!r}：{summary}"
        line = next(
            (ln for ln in summary.splitlines() if ln.strip().split()[:1] == [resource]),
            None,
        )
        assert line is not None, f"HELP_SUMMARY 里没有 {resource} 的行：{summary}"
        actions = [cmd.primary_action for cmd in commands if cmd.primary_action]
        positions = [line.find(action) for action in actions]
        assert all(pos >= 0 for pos in positions), (
            f"HELP_SUMMARY 的 {resource} 行缺少操作：{actions} —— 实际 {line!r}"
        )
        assert positions == sorted(positions), (
            f"HELP_SUMMARY 的 {resource} 行操作顺序与命令表不一致：{line!r}"
        )


def test_help_detail_covers_every_schema_param_name() -> None:
    """反向护栏：schema 里的**每一个**参数名都必须能在 `describe_param` 渲染出来。"""
    problems: list[str] = []
    for cmd in TABLE.specs:
        if cmd.tool is None:
            continue
        for name, prop in cmd.param_schemas.items():
            rendered = cs.describe_param(
                name,
                prop,
                required=name in cmd.schema_required,
                aliases=tuple(alias for alias, real in cmd.alias_map.items() if real == name),
            )
            if f"--{cc.to_kebab(name)}" not in rendered:
                problems.append(f"{cmd.key}: 参数 {name} 未出现在渲染结果里")
    assert not problems, "参数渲染漏项：\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
# `dept rm` 的定位行为（P0 变化 e：位置槽可选 ID + 按名删除改 --name）
# --------------------------------------------------------------------------- #


class TestDeleteDeptLocator:
    """`delete_dept` 的定位面：位置 `<id>` 与 `--name` 二选一，名称不再走位置槽。

    这两条钉的是**声明结果**（`ConsoleCommand("dept", ("rm","remove"), args=(Arg(Target("dept_id", int), positional=True, required=False),))`），
    不是服务层：声明若被改成必填，`dept rm --name 研发部` 会立刻被 arity 拒；
    声明若被改成 `str`，`dept rm 研发部` 就不再报 `invalid_target`。
    """

    @pytest.fixture(autouse=True)
    def _loaded(self):
        funcCore.load_func_tools()
        funcCore.load_container_func_tools()
        yield

    def _resolve(self, tokens: list[str]) -> dict[str, Any]:
        cmd = TABLE.by_path["dept rm"]
        positional, options, _source = cp._parse_options(cmd, tokens)
        pos_args, _inline = ct._resolve_positionals(cmd, positional, options)
        args = ct._option_args(cmd, options)
        return {**pos_args, **args}

    def test_positional_id_maps_to_dept_id(self) -> None:
        """`dept rm 7` ⇒ `{"dept_id": 7}`（正整数按 int 收敛）。"""
        assert self._resolve(["7"]) == {"dept_id": 7}

    def test_name_is_only_accepted_as_option(self) -> None:
        """`dept rm --name 研发部` ⇒ `{"name": "研发部"}`（按名删除改走选项，变化 e）。"""
        assert self._resolve(["--name", "研发部"]) == {"name": "研发部"}

    def test_positional_name_is_rejected_at_cli_layer(self) -> None:
        """`dept rm 研发部` 在**位置解析层**就报 `invalid_target`，并提示改用 `--name`。

        改前：位置槽是「数字当 id、其余当名字」（D8 双定位）；
        改后（变化 e）：位置槽只收正整数，按名删除必须显式 `--name`——零隐式猜测。
        """
        with pytest.raises(cp.ConsoleError) as caught:
            self._resolve(["研发部"])
        assert caught.value.code == "invalid_target", caught.value.code
        assert "--name" in caught.value.message, caught.value.message

    def test_delete_dept_without_locator_is_rejected_by_tool(self) -> None:
        """`delete_dept()` 两个定位都不给 ⇒ 运行时被拒，`error_code == invalid_tool_args`。

        命令层不再替它报错（P0 把「无有效动作」的判定归回工具层），因此这条必须在
        工具层钉住：schema 层因「均可选」拦不住，若运行时那条判断被删，一次
        `delete_dept()` 会走到「删掉整个团队」的分支。
        """
        outcome = asyncio.run(
            funcTools.delete_dept(_context=ToolCallContext(agent_id=1, team_id=1, chat_room=None))
        )

        assert outcome.get("success") is False, f"不给定位竟然成功了：{outcome}"
        assert outcome.get("error_code") == "invalid_tool_args", (
            f"期望 invalid_tool_args（与 schema 层同码），实际 {outcome.get('error_code')!r}"
            f"（{outcome.get('message')!r}）"
        )
        message = outcome.get("message", "")
        assert "dept_id" in message or "name" in message, f"文案没告诉怎么补：{message!r}"


# --------------------------------------------------------------------------- #
# 布尔选项的显式值：`--flag` 与 `--flag=false` 都必须落到 bool
#
# P0 删掉了 `overwrite`（那正是旧判据的探针），改用 `dept rm --dry-run`：
# 它是 schema 里的 `bool=False`，且 `dept rm` 的位置槽可选 ⇒ 单给开关也合法。
# --------------------------------------------------------------------------- #


def _dry_run_options(tokens: list[str]) -> dict[str, Any]:
    cmd = TABLE.by_path["dept rm"]
    _positional, options, _source = cp._parse_options(cmd, tokens)
    return options


def test_boolean_option_bare_flag_is_true() -> None:
    assert _dry_run_options(["--dry-run"])["dry-run"] is True


def test_boolean_option_explicit_false_is_preserved() -> None:
    cmd = TABLE.by_path["dept rm"]
    positional, options, source = cp._parse_options(cmd, ["7", "--dry-run=false"])
    assert positional == ["7"]
    assert source is None
    assert options["dry-run"] is False


def test_boolean_option_invalid_text_is_rejected() -> None:
    cmd = TABLE.by_path["dept rm"]
    with pytest.raises(cp.ConsoleError) as exc:
        cp._parse_options(cmd, ["--dry-run=maybe"])
    assert exc.value.code == "invalid_option_value"


def test_removed_overwrite_option_is_unknown() -> None:
    """`--overwrite` 已随 `save_*` 一起删除（变化 a）：现在必须报 unknown_option。

    这条是反对回流判据：只要有人把 overwrite 加回任何一个命令的参数面，它就红。
    """
    for key in ("dept update", "agent update", "room update", "role update"):
        cmd = TABLE.by_path[key]
        with pytest.raises(cp.ConsoleError) as exc:
            cp._parse_options(cmd, ["--overwrite"])
        assert exc.value.code == "unknown_option", (key, exc.value.code)


def test_signature_of_delete_dept_keeps_both_locators_optional() -> None:
    """`delete_dept` 的 `name` / `dept_id` 必须都带默认值 None（按 id 或按名二选一）。"""
    params = inspect.signature(funcTools.delete_dept).parameters
    assert params["dept_id"].default is None
    assert params["name"].default is None
