"""#220 A7：定位参数报错给改写示例 + help 标注「位置参数」（单元层）。

## 这组判据防的是什么

清单 §A7 实测：`dept get --dept-id 7` → `invalid_argument_count`，报错只说
「收到 0 个」、一个字不提怎么改；而 help 把定位参数列成 `--dept-id <integer>（可选）`
——第一次使用必踩（与 A1 同为最高优先级）。

两层都只做「说清楚」、不放宽（A7 建议 2 的选项回填不做）：

1. 报错层：位置参数解析（P0 起由 `consoleTool._resolve_positionals` 承担，取代
   `_check_arity`）发现 `--选项` 里出现了位置槽绑定的参数 ⇒ 消息追加等价改写
   （`dept get 7`），错误码沿用 `invalid_argument_count`；
2. help 层：位置槽参数行单独标注「位置参数」，且 **create 不标**（`dept create`
   的参数面里没有 `dept_id` 了——P0 把新建与更新彻底拆开，变化 a）。

真入口（room/dept 真库、`dept get --dept-id <id>` 实跑）见
`tests/integration/test_funcToolService/test_console_locator_hint.py`。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleRegistry
from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct


def _resolve(command: str) -> list[str]:
    """把一条命令 tokenize 成「剥掉命令名之后」的 rest（两段式 / 顶层命令都适用）。"""
    tokens = cp._tokenize(command)
    cmd, err = consoleRegistry.get_table().resolve(tokens)
    assert cmd is not None, err
    return tokens[1:] if not cmd.actions else tokens[2:]


def _run(command: str) -> None:
    """走真流水线的前三步（分词 → 选项解析 → 位置槽解析），在转发前结束。

    P0 后 `_check_arity` 并入 `_resolve_positionals`，因此这里直接调它：hint 的值提取
    依赖 `_parse_options`（别名归一、首次原名做键）与 `normalize_option`，走真链才能
    咬住别名混写等分叉。
    """
    tokens = cp._tokenize(command)
    cmd, err = consoleRegistry.get_table().resolve(tokens)
    assert cmd is not None, err
    rest = _resolve(command)
    positional, options, _input_source = cp._parse_options(cmd, rest)
    ct._resolve_positionals(cmd, positional, options)


# --------------------------------------------------------------------------- #
# ① 报错层：改写示例（需求 1）
# --------------------------------------------------------------------------- #


class TestLocatorRewriteHint:
    def test_option_form_gets_rewrite_example(self) -> None:
        """`dept get --dept_id 7`（兼容期旧名）⇒ invalid_argument_count + 等价写法。

        旧名输入也要给改写，且报错展示 **kebab 主形态**（--dept-id）——兼容期两种写法
        都能走到这条报错，展示面统一。
        """
        with pytest.raises(cp.ConsoleError) as caught:
            _run("dept get --dept_id 7")  # 兼容期旧名输入
        exc = caught.value
        assert exc.code == "invalid_argument_count", f"错误码不该变（沿用口径）：{exc.code!r}"
        assert "dept get 7" in exc.message, f"没给等价改写示例：{exc.message!r}"
        assert "--dept-id" in exc.message, f"没点出是定位参数写法问题：{exc.message!r}"
        assert "位置参数" in exc.message, f"没说清定位只接受位置形态：{exc.message!r}"

    def test_option_form_via_inline_value(self) -> None:
        """`--dept-id=7` 同样要拿到改写示例（inline 与分 token 两种写法等价）。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _run("dept get --dept_id=7")  # 兼容期旧名 inline 值
        assert "dept get 7" in caught.value.message

    def test_name_form_locator_gets_rewrite(self) -> None:
        """纯名位置槽（`role get --role-name x`）也要给改写。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _run("role get --role_name tester")
        assert "role get tester" in caught.value.message

    def test_save_command_with_locator_option(self) -> None:
        """`task update --task-id 187 --note x`（payload=True）同样给改写。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _run("task update --task-id 187 --note x")
        message = caught.value.message
        assert "task update 187" in message, f"payload 命令的定位选项写法没给改写：{message!r}"
        assert "照原样保留" in message, f"没说明其余 --选项 怎么办：{message!r}"

    def test_missing_target_without_locator_option_stays_clean(self) -> None:
        """没写定位选项的纯缺参报错**不带**改写提示（避免无中生有的建议）。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _run("dept get")
        assert "等价写法" not in caught.value.message, (
            f"没有定位选项却给了改写：{caught.value.message!r}"
        )

    def test_non_locator_option_does_not_trigger_hint(self) -> None:
        """非定位选项（`task ls --assignee 8`，该命令没有位置槽）不触发改写。"""
        _run("task ls --assignee 8")  # 不抛错即通过


# --------------------------------------------------------------------------- #
# ② help 层：位置参数标注（需求 2）
# --------------------------------------------------------------------------- #


class TestHelpPositionalLabel:
    def test_get_command_locator_line_is_marked(self) -> None:
        """`dept get` 的 `--dept-id` 行必须标「位置参数」且含等价写法。"""
        detail = ct.HELP_DETAIL["dept get"]
        line = [ln for ln in detail.splitlines() if ln.lstrip().startswith("--dept-id")]
        assert line, f"dept get 参数清单里没有 --dept-id 行：\n{detail}"
        assert "位置参数" in line[0], f"定位行没标位置参数：{line[0]!r}"
        assert "dept get <" in line[0], f"没给等价写法占位：{line[0]!r}"

    def test_payload_command_locator_line_is_marked(self) -> None:
        """`task update` 的 `--task-id` 行同样标注（payload=True 的位置槽）。"""
        detail = ct.HELP_DETAIL["task update"]
        line = [ln for ln in detail.splitlines() if ln.lstrip().startswith("--task-id")]
        assert line and "位置参数" in line[0], f"task update 的 --task-id 行没标注：{line!r}"

    def test_create_has_no_positional_slot_at_all(self) -> None:
        """`dept create` 的参数面里**没有** `dept_id`：P0 把新建与更新彻底拆开后，
        「create 时顺带传 ID 走更新语义」这条通道已随 `save_dept` 一起删除（变化 a）。

        旧判据守的是「create 的 locator 行不许标位置参数」；现在的前提（该行存在）
        本身不成立，故改为直接钉「它不该存在」，避免 `--dept-id` 悄悄回流到 create。
        """
        detail = ct.HELP_DETAIL["dept create"]
        assert "--dept-id" not in detail, f"dept create 又出现了定位参数：\n{detail}"
        assert "--overwrite" not in detail, f"dept create 又出现了已删除的 overwrite：\n{detail}"

    def test_non_locator_param_is_not_marked(self) -> None:
        """普通选项（--note / --assignee）不标位置参数（防标注扩散）。"""
        detail = ct.HELP_DETAIL["task update"]
        line = [ln for ln in detail.splitlines() if ln.lstrip().startswith("--note")]
        assert line and "位置参数" not in line[0], f"--note 被误标：{line!r}"

    def test_positional_label_via_schema_unit(self) -> None:
        """渲染函数单点：传入 label 就渲染、不传就不渲染（不隐式推断）。"""
        prop = {"type": "integer", "description": "部门 id"}
        plain = cs.describe_param("dept_id", prop, required=False)
        marked = cs.describe_param("dept_id", prop, required=False, positional_label="位置参数：写在命令名后")
        assert "位置参数" not in plain
        assert "位置参数" in marked
        # label 渲染在别名之后、必填标记之前，行为与 _describe_params 实际顺序一致
        assert marked.index("位置参数") < marked.index("可选")
