"""#220 A7：定位参数报错给改写示例 + help 标注「位置参数」（单元层）。

## 这组判据防的是什么

清单 §A7 实测：`dept get --dept-id 7` → `invalid_argument_count`，报错只说
「收到 0 个」、一个字不提怎么改；而 help 把 locator 列成 `--dept-id <integer>（可选）`
——第一次使用必踩（与 A1 同为最高优先级）。

两层都只做「说清楚」、不放宽（A7 建议 2 的选项回填不做，见任务边界）：
  1. 报错层：`_check_arity` 发现 options 里有 locator 参数 ⇒ 消息追加等价改写
     （`dept get 7`），错误码沿用 `invalid_argument_count`；
  2. help 层：locator 参数行单独标注「位置参数」，且 **create 不标**
     （`dept create --dept-id 5` 是合法的更新语义一并传入，标了就是假信息）。

真入口（room/dept 真库、`dept get --dept-id <id>` 实跑）见
`tests/integration/test_funcToolService/test_console_locator_hint.py`。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct


def _parsed(command: str) -> cp.ParsedCommand:
    """走真解析链到 `_check_arity` 之前（tokenizer → _parse_options → ParsedCommand）。

    不直接 mock ParsedCommand：hint 的值提取依赖 `spec.normalize_option` 与
    `_parse_options` 的「首次原名做键」约定，走真链才能咬住别名混写等分叉。
    命令 token 的前两段是资源与操作（`dept get ...`），位置参数从第 3 段起。
    """
    spec = cc.COMMAND_INDEX["dept get"]
    rest = cp._tokenize(command)[2:]
    positional, options, input_source = cp._parse_options(spec, rest)
    return cp.ParsedCommand(
        spec,
        positional=positional,
        options=options,
        input_source=input_source,
        raw_tokens=rest,
    )


# --------------------------------------------------------------------------- #
# ① 报错层：改写示例（需求 1）
# --------------------------------------------------------------------------- #


class TestLocatorRewriteHint:
    def test_option_form_gets_rewrite_example(self) -> None:
        """`dept get --dept_id 7`（兼容期旧名）⇒ invalid_argument_count + 等价写法。

        旧名输入也要给改写，且报错展示 **kebab 主形态**（--dept-id）——兼容期两种
        写法都能走到这条报错，展示面统一。
        """
        parsed = _parsed("dept get --dept_id 7")  # 兼容期旧名输入
        with pytest.raises(cp.ConsoleError) as caught:
            ct._check_arity(parsed)
        exc = caught.value
        assert exc.code == "invalid_argument_count", f"错误码不该变（沿用口径）：{exc.code!r}"
        assert "dept get 7" in exc.message, f"没给等价改写示例：{exc.message!r}"
        assert "--dept-id" in exc.message, f"没点出是定位参数写法问题：{exc.message!r}"
        assert "位置参数" in exc.message, f"没说清定位只接受位置形态：{exc.message!r}"

    def test_option_form_via_inline_value(self) -> None:
        """`--dept-id=7` 同样要拿到改写示例（inline 与分 token 两种写法等价）。"""
        parsed = _parsed("dept get --dept_id=7")  # 兼容期旧名 inline 值
        with pytest.raises(cp.ConsoleError) as caught:
            ct._check_arity(parsed)
        assert "dept get 7" in caught.value.message

    def test_name_form_locator_gets_rewrite(self) -> None:
        """纯名 locator（role get --role_name x）与 kind=both 的名字形态都要给改写。"""
        spec = cc.COMMAND_INDEX["role get"]
        positional, options, input_source = cp._parse_options(
            spec, ["--role_name", "tester"]
        )
        parsed = cp.ParsedCommand(
            spec, positional=positional, options=options,
            input_source=input_source, raw_tokens=["--role_name", "tester"],
        )
        with pytest.raises(cp.ConsoleError) as caught:
            ct._check_arity(parsed)
        assert "role get tester" in caught.value.message

    def test_save_command_with_locator_option(self) -> None:
        """`task update --task-id 187 --note x`（SAVE+accepts_input）同样给改写。"""
        spec = cc.COMMAND_INDEX["task update"]
        positional, options, input_source = cp._parse_options(
            spec, ["--task-id", "187", "--note", "x"]
        )
        parsed = cp.ParsedCommand(
            spec, positional=positional, options=options,
            input_source=input_source, raw_tokens=["--task-id", "187", "--note", "x"],
        )
        with pytest.raises(cp.ConsoleError) as caught:
            ct._check_arity(parsed)
        assert "task update 187" in caught.value.message, (
            f"SAVE 命令的 locator 选项写法没给改写：{caught.value.message!r}"
        )
        assert "照原样保留" in caught.value.message, "没说明其余 --选项 怎么办"

    def test_missing_target_without_locator_option_stays_clean(self) -> None:
        """没写 locator 选项的纯缺参报错**不带**改写提示（避免无中生有的建议）。"""
        parsed = _parsed("dept get")
        with pytest.raises(cp.ConsoleError) as caught:
            ct._check_arity(parsed)
        assert "等价写法" not in caught.value.message, (
            f"没有 locator 选项却给了改写：{caught.value.message!r}"
        )

    def test_non_locator_option_does_not_trigger_hint(self) -> None:
        """非定位选项（如 task ls --assignee）不触发改写。"""
        spec = cc.COMMAND_INDEX["task ls"]
        positional, options, input_source = cp._parse_options(spec, [])
        parsed = cp.ParsedCommand(
            spec, positional=positional, options=options,
            input_source=input_source, raw_tokens=[],
        )
        ct._check_arity(parsed)  # 不抛错，正常通过


# --------------------------------------------------------------------------- #
# ② help 层：位置参数标注（需求 2）
# --------------------------------------------------------------------------- #


class TestHelpPositionalLabel:
    def test_get_command_locator_line_is_marked(self) -> None:
        """`dept get` 的 `--dept-id` 行必须标「位置参数」且含等价写法。"""
        detail = ct.HELP_DETAIL["dept get"]
        line = [ln for ln in detail.splitlines() if ln.lstrip().startswith("--dept-id")]
        assert line, f"dept get 参数清单里没有 --dept-id 行：\n{detail}"
        assert "位置参数" in line[0], f"locator 行没标位置参数：{line[0]!r}"
        assert "dept get <" in line[0], f"没给等价写法占位：{line[0]!r}"

    def test_save_update_locator_line_is_marked(self) -> None:
        """`task update` 的 `--task-id` 行同样标注（SAVE+needs_target）。"""
        detail = ct.HELP_DETAIL["task update"]
        line = [ln for ln in detail.splitlines() if ln.lstrip().startswith("--task-id")]
        assert line and "位置参数" in line[0], f"task update 的 --task-id 行没标注：{line!r}"

    def test_create_is_not_marked(self) -> None:
        """`dept create` 的 `--dept-id` 行**不标**——create 里它是合法选项
        （更新语义一并传入），标「不接受选项写法」就是假信息（变异 M2' 的反向护栏）。"""
        detail = ct.HELP_DETAIL["dept create"]
        line = [ln for ln in detail.splitlines() if ln.lstrip().startswith("--dept-id")]
        assert line, f"dept create 参数清单里没有 --dept-id 行：\n{detail}"
        assert "位置参数" not in line[0], f"create 的 locator 行被误标：{line[0]!r}"

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
        # label 渲染在别名之后、必填标记之前，行为与 _help_detail 实际顺序一致
        assert marked.index("位置参数") < marked.index("可选")
