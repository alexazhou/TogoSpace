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
        """`dept get --dept_id 7`（兼容期旧名）⇒ `unknown_option` + 等价写法。

        错误码变化（#334 / C-01）：改前靠「位置参数个数不足」在**位置解析**这一步间接拒，
        码是 `invalid_argument_count`；C-01 之后位置槽的 `--选项` 写法在**选项解析**层就被
        拒，码是 `unknown_option`（`min_pos=0` 的 `dept rm` 走了同一条路，不再放行）。

        但改写提示必须照旧给出，且展示 **kebab 主形态**（--dept-id）——兼容期两种写法都能
        走到这条报错，展示面统一。没有提示的裸 `unknown_option` 等于把用户的路堵死。
        """
        with pytest.raises(cp.ConsoleError) as caught:
            _run("dept get --dept_id 7")  # 兼容期旧名输入
        exc = caught.value
        assert exc.code == "unknown_option", f"C-01 后应在选项层拒绝：{exc.code!r}"
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
    """方案 B（#323 / C-08）：位置参数单列「位置参数：」段，名字复用签名 token。

    旧契约是「`--dept-id` 行加一段『位置参数：值必须写在命令名后面…』标注」——
    同一件事两个 token（签名 `<id>` vs 行名 `--dept-id`）。新契约下：
    段标题 → 行名 = 签名 token（不带 `--`）→ 短注说明被拒绝的选项写法。
    """

    def test_get_command_positional_section_uses_signature_token(self) -> None:
        """`dept get`：位置段行名 `<id>`（与签名逐字一致），且不再有 `--dept-id` 行。"""
        detail = ct.HELP_DETAIL["dept get"]
        assert "位置参数：" in detail, f"dept get 没有「位置参数：」段：\n{detail}"
        section = detail.split("位置参数：", 1)[1]
        token_line = [ln for ln in section.splitlines() if ln.startswith("  <id>")]
        assert token_line, f"位置段里没有 `<id>` 行：\n{detail}"
        # 签名与行名同源：用法行里也必须是 `<id>`
        assert "dept get <id>" in detail.splitlines()[0], detail.splitlines()[0]
        # 旧形态不许回流：参数清单里不能再出现 `--dept-id` 选项行
        assert not [ln for ln in detail.splitlines() if ln.lstrip().startswith("--dept-id")], (
            f"`--dept-id` 选项行又出现了：\n{detail}"
        )
        # 短注点名被拒绝的写法（与签名 token 同源）
        assert "不接受 --dept-id 选项写法" in detail, detail

    def test_payload_command_positional_section_uses_signature_token(self) -> None:
        """`task update`（payload=True 的位置槽）：同样进位置段、行名 `<id>`。"""
        detail = ct.HELP_DETAIL["task update"]
        section = detail.split("位置参数：", 1)[1]
        line = [ln for ln in section.splitlines() if ln.startswith("  <id>")]
        assert line and "（必填）" in line[0], f"task update 的 <id> 行没标必填：{line!r}"
        assert not [ln for ln in detail.splitlines() if ln.lstrip().startswith("--task-id")]

    def test_container_note_two_required_slots_marked_required(self) -> None:
        """C-09 回归钉：`container note` 两个位置槽都是必填（`Arg(required=True)`），
        改造前被印成「（可选）」——根因是拿了 schema `required`，而工具签名参数全有
        默认值 ⇒ `schema_required` 恒空集。这里钉「声明即判据」：把判据换回 schema
        required，本用例必须转红（变异实跑见 #323 报告）。
        """
        detail = ct.HELP_DETAIL["container note"]
        section = detail.split("位置参数：", 1)[1].split("选项：", 1)[0]
        for token in ("<container_id>", "<note>"):
            line = [ln for ln in section.splitlines() if ln.startswith(f"  {token} ")]
            assert line, f"container note 位置段缺 {token} 行：\n{detail}"
            assert "（必填）" in line[0], f"{token} 没标必填：{line[0]!r}"
        # 签名同源：用法行里两个槽都在、且不带 `--`
        usage = detail.splitlines()[0]
        assert usage.startswith("container note <container_id> <note>"), usage

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
        """渲染函数单点：位置参数用 `describe_positional`（token 不带 `--`）。"""
        prop = {"type": "integer", "description": "部门 id"}
        rendered = cs.describe_positional("<id>", prop, required=True)
        assert rendered.startswith("<id> <integer>（必填）"), rendered
        assert "部门 id" in rendered
        # 方案 B 之后 `describe_param` 的 positional_label 分支不再是主路径，
        # 但仍须可用（mcp 子树等旧调用点不因此炸）
        legacy = cs.describe_param("dept_id", prop, required=False, positional_label="位置参数：写在命令名后")
        assert legacy.startswith("--dept-id <integer>") and "位置参数" in legacy
