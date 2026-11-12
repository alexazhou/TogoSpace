"""#196 P2 结构护栏：命令表是唯一事实源。

## 这组判据防的是什么

P2 把「加一条命令要改四处」（路由 if 分支、help 正文、选项白名单、布尔开关集合）
收敛成「只改命令表一行」。收益是**少三处可能漂移的地方**，但漂移本身不会让任何
行为用例变红 —— 加了命令忘记加 help 行、schema 加了参数但 help 没跟上，都能全绿。

所以这里用**派生一致性**钉住：把两份独立算出来的东西对比，而不是对比「我手抄的
期望值」。手抄的期望值本身就是第三份事实源，会和实现一起漂。

三条护栏：
  1. help 的命令键集合 == 命令表的键集合（加命令不写 help / 删命令留 help 都红）；
  2. help 里列出的每个 `--参数` 都能在该命令的 schema properties 里找到（反之亦然）；
  3. `HELP_SUMMARY` 里的资源与操作 == 命令表派生结果。

## 另外两条是 `delete_dept` 的定位判据

P2 之前 console 自己预查部门再删（`_plan_dept_rm`），P2 改成纯转发后，定位只剩
两种形态：按 id、按名字。这里钉住「单给 id 能删」与「什么都不给被拒」——
后者是 `delete_dept` 的 `required` 已被放开（P1）之后的**新增风险**：`required`
空了，schema 层不再挡「两个定位都不给」，必须由运行时兜住并给 `invalid_tool_args`。
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct
from service.funcToolService import core as funcCore
from service.funcToolService import tools as funcTools
from service.roomService import ToolCallContext


# --------------------------------------------------------------------------- #
# 护栏 1~3：help 与命令表的派生一致性
# --------------------------------------------------------------------------- #


def test_help_keys_match_command_table() -> None:
    """help 的键集合必须与命令表**完全相同**（双向）。

    单向比较会漏掉一半：只查「命令表 ⊆ help」时，删掉一条命令但 help 里留着旧行
    不红；只查「help ⊆ 命令表」时，加了命令不写 help 也不红。
    """
    spec_keys = {spec.key for spec in cc.COMMAND_SPECS}
    help_keys = set(ct.HELP_DETAIL)

    assert spec_keys == help_keys, (
        f"命令表与 help 不一致：\n"
        f"  只在命令表（help 缺）：{sorted(spec_keys - help_keys)}\n"
        f"  只在 help（命令表已删）：{sorted(help_keys - spec_keys)}"
    )


def test_help_lists_exactly_the_schema_params() -> None:
    """help 里列出的 `--参数` 必须与 schema properties **逐名相同**。

    这是「选项面从 schema 派生」这件事的**可观测结果**：派生逻辑一旦回退成手抄、
    或者 schema 加了参数但 help 渲染漏了，这里立刻红。别名也要在——用户在 help 上
    看到的写法必须真能用（`--kw` 在 `options` 白名单里）。
    """
    problems: list[str] = []

    for spec in cc.COMMAND_SPECS:
        if not spec.legacy_tool:
            continue
        props = set(spec.param_names)
        # `HELP_DETAIL[key]` 是**一段文本**（不是行列表）：按行正则捞 `  --name`。
        listed = set(
            re.findall(r"^  --([A-Za-z_][\w-]*)", ct.HELP_DETAIL[spec.key], flags=re.MULTILINE)
        )

        missing = props - listed
        extra = listed - set(spec.options)
        if missing:
            problems.append(f"{spec.key}: schema 有但 help 没列 {sorted(missing)}")
        if extra:
            problems.append(f"{spec.key}: help 列了但选项白名单没有 {sorted(extra)}")

    assert not problems, "help 与 schema 参数面不一致：\n" + "\n".join(problems)


def test_help_summary_is_derived_from_command_table() -> None:
    """`HELP_SUMMARY` 的资源与操作 == 命令表派生结果（按资源分组、保表内顺序）。"""
    expected: dict[str, list[str]] = {}
    for spec in cc.COMMAND_SPECS:
        expected.setdefault(spec.resource, []).append(spec.action or spec.key)

    summary = ct.HELP_SUMMARY
    for resource in expected:
        assert resource in summary, f"HELP_SUMMARY 缺少资源 {resource!r}：{summary}"

    # 每个资源行里必须出现该资源的全部操作（顺序也按命令表）。
    # 顶层命令（`reload`）没有 action，摘要里就是单独一个词，所以按 `resource` 前缀取行。
    for resource, actions in expected.items():
        line = next(
            (ln for ln in summary.splitlines() if ln.strip().split()[:1] == [resource]),
            None,
        )
        assert line is not None, f"HELP_SUMMARY 里没有 {resource} 的行：{summary}"
        positions = [line.find(action) for action in actions if action]
        assert all(pos >= 0 for pos in positions), (
            f"HELP_SUMMARY 的 {resource} 行缺少操作：{actions} —— 实际 {line!r}"
        )
        assert positions == sorted(positions), (
            f"HELP_SUMMARY 的 {resource} 行操作顺序与命令表不一致：{line!r}"
        )


def test_help_detail_covers_every_schema_param_name() -> None:
    """反向护栏：schema 里的**每一个**参数名都必须能在 `describe_param` 渲染出来。

    上一条比的是「help 文本 vs 白名单」，这条比的是「渲染函数 vs schema」——
    两处各自独立算，任一处漏参数都会红。
    """
    problems: list[str] = []
    for spec in cc.COMMAND_SPECS:
        if not spec.legacy_tool:
            continue
        schemas = cs.param_schemas(spec.legacy_tool)
        for name in schemas:
            rendered = cs.describe_param(
                name,
                schemas[name],
                required=name in (cs.required_params(spec.legacy_tool)),
                aliases=tuple(
                    alias for alias, real in spec.alias_map.items() if real == name
                ),
            )
            if f"--{name}" not in rendered:
                problems.append(f"{spec.key}: 参数 {name} 未出现在渲染结果里")
    assert not problems, "参数渲染漏项：\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
# `delete_dept` 的两条定位判据（P2：纯转发后的定位面）
# --------------------------------------------------------------------------- #


class TestDeleteDeptByP2Locator:
    """`delete_dept` 两条定位判据：单给 id 能删、什么都不给被拒。

    这两条钉的是**命令表 locator 声明**的结果，不是服务层：
    `dept rm` 的 `locator=_loc("dept_id", "name")` ⇒ `dept rm 7` 传 `dept_id=7`。
    若有人把 locator 改成只填 `name`，第一条立刻红（会按名字找 "7" 找不到）。
    """

    @pytest.fixture(autouse=True)
    def _loaded(self):
        funcCore.load_func_tools()
        funcCore.load_container_func_tools()
        yield

    def test_delete_dept_by_id_alone_deletes(self) -> None:
        """`delete_dept(dept_id=N)` 单靠 id 就能删（不要求同时给 name）。"""
        signature = inspect.signature(funcTools.delete_dept)
        params = signature.parameters

        assert "dept_id" in params, "delete_dept 必须支持按 id 定位"
        assert params["dept_id"].default is None, (
            "dept_id 必须有默认值 None —— 否则「按 id 删」与「按名删」无法二选一"
        )
        assert params["name"].default is None, (
            "name 必须有默认值 None —— 同上；这是 P1 放开 required 的前提"
        )

        # 命令表必须把位置参数映射到 dept_id（而不是只映射 name）。
        spec = cc.COMMAND_INDEX["dept rm"]
        assert spec.locator is not None, "dept rm 必须声明 locator"
        assert spec.locator.id_param == "dept_id", (
            f"dept rm 的 id_param 必须是 dept_id，当前 {spec.locator.id_param!r}"
        )
        assert spec.locator.name_param == "name", (
            f"dept rm 的 name_param 必须是 name，当前 {spec.locator.name_param!r}"
        )

        # 定位翻译：纯数字 ⇒ 当 id；非数字 ⇒ 当 name（D8 兼容方案）。
        translated = ct._locator_args(spec, "7", require_positive_id=False)
        assert translated == {"dept_id": 7}, translated
        translated = ct._locator_args(spec, "研发部", require_positive_id=False)
        assert translated == {"name": "研发部"}, translated

    def test_delete_dept_without_locator_is_rejected_with_schema_code(self) -> None:
        """`delete_dept()` 两个定位都不给 ⇒ 被拒，且 `error_code == invalid_tool_args`。

        ⚠️ 这是 P1 放开 `required` 之后的**新增风险点**：schema 层不再拦「不给定位」，
        全靠运行时那条 `if dept_id is None and name is None`。若有人顺手删掉它，
        schema 与运行时都不挡 ⇒ 一次 `delete_dept()` 会走到「删掉整个团队」的分支。
        所以必须在**运行时层**钉住，并且要求它与 schema 层**同码**
        （小马哥 12:02 硬条件 1：调用方一个码就能判「补参数重试」）。
        """
        result = funcCore.run_tool_call_sync_for_tests = None  # 占位，见下
        del result

        coro = funcTools.delete_dept(
            _context=ToolCallContext(agent_id=1, team_id=1, chat_room=None)
        )
        outcome = _run(coro)

        assert outcome.get("success") is False, f"不给定位竟然成功了：{outcome}"
        assert outcome.get("error_code") == "invalid_tool_args", (
            f"期望 invalid_tool_args（与 schema 层同码），实际 {outcome.get('error_code')!r}"
            f"（{outcome.get('message')!r}）"
        )
        # 文案必须点名「怎么补」——只说「参数错」不够模型自纠。
        message = outcome.get("message", "")
        assert "dept_id" in message or "name" in message, f"文案没告诉怎么补：{message!r}"


def _run(coro: Any) -> dict:
    """在同步用例里跑一个协程（本文件的判据都是同步用例）。"""
    import asyncio

    return asyncio.run(coro)
