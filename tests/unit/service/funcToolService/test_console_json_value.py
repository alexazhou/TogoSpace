"""#219 JSON 值保护 + help 传参形态标注 + 标量重复建议修正（单元层）。

## 这组判据防的是什么

#218 收敛了「重复选项」语义，但值形态仍是**无条件 CSV**：模型把 JSON 数组直接塞进
选项（`--member-names '["a","b"]'`）会被拆成 `["a` / `"b"]` 两段，报成
「未找到成员: ["a」——错得看不出原因（console 工具改进建议清单 §A1 实测）。
同时 #218 的 `duplicate_option` 报错无条件建议 `--x a,b`，对 integer/object
**不可执行**（实测：integer ⇒ invalid_target，object ⇒ invalid_option_value）——
不可执行的建议比不给更坏。

三条防线：
  1. 数组型选项**逐值段**判断形态：`[` 开头按 JSON 数组解析，否则按 CSV；
  2. object 型只收 JSON object 字面量，错误区分「不是 JSON」与「JSON 语法坏」；
  3. 标量重复报错不再携带不可执行的 `suggestion`；help 参数行标注传参形态。

## 为什么钉在解析/转换层而不是只测 console 入口

形态判断是 `_convert_option` 一处实现的纯函数行为，单元层就能把三种写法的
**等价性**（CSV / JSON / 重复聚合产出同一列表）和两种报错的**区分**钉死；
真入口的可达性由 `tests/integration/test_funcToolService/test_console_json_value.py`
补（room create 走通 + task ls 三向），两层各管各的失效面。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleRegistry
from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct


def _convert(kind: str, raw, *, name: str = "x", command: str = "demo"):
    return ct._convert_option(raw, kind=kind, name=name, command=command)


# --------------------------------------------------------------------------- #
# ① JSON 值保护：数组型 `[` 开头按 JSON 解析（需求 1）
# --------------------------------------------------------------------------- #


class TestArrayJsonValue:
    def test_json_array_literal_parses_to_strings(self) -> None:
        """`--member-names '["a","b"]'` 必须得到 ['a','b']，而不是拆成 `["a` / `"b"]`。"""
        assert _convert("array", '["a","b"]', name="member_names", command="room create") == ["a", "b"]

    def test_csv_semantics_untouched(self) -> None:
        """既有 CSV 语义照旧（本单不改的边界）：逗号拆分 / trim / 空段丢弃 / 去重保序。"""
        assert _convert("array", "a,b") == ["a", "b"]
        assert _convert("array", " a , ,b,a") == ["a", "b"]

    def test_mixed_segments_are_decided_per_segment(self) -> None:
        """**逐值段**判断：JSON 段与 CSV 段混写都能收（#218 聚合后 raw 是值段列表）。

        变异「只看整体首字符定形态」在这条必红：整体首段是 `[`，第二段 `c` 就会被
        当成 JSON 去解析（报错）或被 CSV 拆坏，`['["a","b"]','c']` 无法完整落地。
        """
        assert _convert("array", ['["a","b"]', "c"]) == ["a", "b", "c"]
        assert _convert("array", ["a,b", '["c","d"]']) == ["a", "b", "c", "d"]

    def test_repeated_csv_equals_json_equals_csv(self) -> None:
        """三者叠加等价（需求 3，钉在转换层）：CSV / JSON / 重复聚合产出**同一个列表**。

        变异「聚合只取首段」在这条必红：`['a','b']` 只取首段得 ['a'] ≠ ['a','b']。
        """
        assert _convert("array", "a,b") == ["a", "b"]
        assert _convert("array", ["a", "b"]) == ["a", "b"]
        assert _convert("array", '["a","b"]') == ["a", "b"]

    def test_invalid_json_says_json_and_points_to_csv(self) -> None:
        """非法 JSON 的提示要**区分开**：告诉用户这是 JSON 解析失败、并给出可走的路。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _convert("array", '["a","b"', name="member_names", command="room create")
        exc = caught.value
        assert exc.code == "invalid_option_value"
        assert "JSON" in exc.message, f"提示没说是 JSON 解析失败：{exc.message!r}"
        assert "逗号" in exc.message and "payload" in exc.message, (
            f"提示没给出替代写法（逗号 / --from payload）：{exc.message!r}"
        )

    def test_broken_json_syntax_reports_position(self) -> None:
        """JSON 语法坏（不是缺 `[`）也走 JSON 提示，并带行列号。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _convert("array", '[a,b]')
        assert caught.value.code == "invalid_option_value"
        assert "JSON" in caught.value.message

    def test_json_non_array_element_is_rejected_not_silently_stringified(self) -> None:
        """元素必须是字符串：把 `1` 静默 `str()` 成 `"1"` 会造出下游看不懂的值。"""
        with pytest.raises(cp.ConsoleError) as caught:
            _convert("array", '["a",1]')
        assert caught.value.code == "invalid_option_value"
        assert "字符串" in caught.value.message

    def test_json_scalar_in_brackets_rejected(self) -> None:
        """`[` 开头但解析结果不是数组（如 `[1]` 的元素错、`"..."` 不适用）⇒ 报错不静默。"""
        with pytest.raises(cp.ConsoleError):
            _convert("array", '["a",null]')
        with pytest.raises(cp.ConsoleError):
            _convert("array", '[["a"]]')


class TestObjectConsistency:
    def test_valid_object(self) -> None:
        assert _convert("object", '{"k":"v"}') == {"k": "v"}

    def test_not_json_shape_error_is_distinct_from_syntax_error(self) -> None:
        """非 `{` 开头 ⇒「形态不对」（该加 `{`）；语法坏 ⇒「解析失败」（该修语法）。

        #218 之前两者混成一条「解析失败」，改法完全不同却给同一个提示。
        """
        with pytest.raises(cp.ConsoleError) as caught:
            _convert("object", "k=v")
        assert caught.value.code == "invalid_option_value"
        assert "`{`" in caught.value.message, f"没提示要 JSON object 字面量：{caught.value.message!r}"
        assert "payload" in caught.value.message

        with pytest.raises(cp.ConsoleError) as caught:
            _convert("object", '{"k":}')
        assert caught.value.code == "invalid_option_value"
        assert "解析失败" in caught.value.message

    def test_object_requires_dict(self) -> None:
        with pytest.raises(cp.ConsoleError):
            _convert("object", '[1,2]')


# --------------------------------------------------------------------------- #
# ② help 传参形态标注（需求 2）：单点渲染 describe_param，detail 派生可见
# --------------------------------------------------------------------------- #


class TestHelpShapeAnnotation:
    def test_array_param_line_carries_shape_note(self) -> None:
        """`room create`（P0 后是 `create_room`）的 `--member-names <array>` 行必须写明 CSV / JSON / 重复等价。"""
        lines = cs.describe_tool_params("create_room")
        member_lines = [line for line in lines if line.startswith("--member-names")]
        assert member_lines, f"room create 参数清单里没有 member_names 行：{lines!r}"
        line = member_lines[0]
        assert "--member-names <array>" in line
        assert "CSV" in line and "JSON" in line, f"array 行没写传参形态：{line!r}"
        assert "重复" in line, f"array 行没提可重复出现：{line!r}"

    def test_object_param_line_carries_shape_note(self) -> None:
        """`mcp call` 的 `--args <object>` 行必须写明 JSON object 字面量与 --from payload。"""
        lines = cs.describe_tool_params("openapi_call")
        args_lines = [line for line in lines if line.startswith("--args")]
        assert args_lines, f"mcp call 参数清单里没有 args 行：{lines!r}"
        line = args_lines[0]
        assert "--args <object>" in line
        assert "JSON object" in line and "payload" in line, f"object 行没写传参形态：{line!r}"

    def test_scalar_and_boolean_lines_are_untouched(self) -> None:
        """string / boolean 参数行**不加**形态注（加了是噪音，M3 的反向护栏）。"""
        lines = cs.describe_tool_params("list_tasks")
        assignee_lines = [line for line in lines if line.startswith("--assignee")]
        assert assignee_lines and "JSON" not in assignee_lines[0], (
            f"标量参数行不该带形态标注：{assignee_lines[0]!r}"
        )

    def test_help_detail_derives_the_annotation(self) -> None:
        """真 help 输出（HELP_DETAIL 派生链）里也要能看到标注——防止只改了渲染函数、
        派生链别处还有第二份参数行渲染。"""
        detail = ct.HELP_DETAIL["room create"]
        assert any(
            line.lstrip().startswith("--member-names") and "JSON" in line
            for line in detail.splitlines()
        ), f"room create 的 help 详情没带 member_names 形态标注：\n{detail}"

    def test_help_summary_states_combination_rule(self) -> None:
        """摘要里的全局重复规则要提「CSV / JSON 两种写法等价」（需求 3 的叠加规则）。"""
        summary = ct.HELP_SUMMARY
        assert "数组型" in summary and ("JSON" in summary or "CSV" in summary), (
            f"help 摘要没写数组型的两种传参写法：{summary!r}"
        )


# --------------------------------------------------------------------------- #
# ③ D2：标量重复报错不再携带不可执行的 suggestion
# --------------------------------------------------------------------------- #


class TestScalarDuplicateNoSuggestion:
    def test_scalar_duplicate_has_no_suggestion_field(self) -> None:
        """标量（string）重复 ⇒ duplicate_option，且 payload 里**没有** suggestion 字段。

        #218 无条件拼 `--x a,b`，把不可执行的写法塞给了 integer/object（实测必报
        invalid_target / invalid_option_value）。变异「改回无条件拼」在这条必红。
        """
        spec = consoleRegistry.get_table().by_path["task update"]
        with pytest.raises(cp.ConsoleError) as caught:
            cp._parse_options(spec, ["187", "--note", "第一次", "--note", "第二次"])
        exc = caught.value
        payload = cp._error_payload(exc)
        assert exc.code == "duplicate_option"
        assert "suggestion" not in payload, (
            f"标量重复不该带 suggestion（对 integer/object 不可执行）：{payload.get('suggestion')!r}"
        )
        assert "--note a,b" not in exc.message, (
            f"报错文案里不该出现对 string 才成立的逗号写法：{exc.message!r}"
        )
        # 旧用例的可操作信息保留：点名选项 + 说明标量语义（test_console_task 依赖）。
        assert "--note" in exc.message and "标量" in exc.message

    def test_scalar_duplicate_message_says_scalar_once(self) -> None:
        """integer 同样走「只能给一次」的提示，而不是任何逗号建议。"""
        spec = consoleRegistry.get_table().by_path["task ls"]
        with pytest.raises(cp.ConsoleError) as caught:
            cp._parse_options(spec, ["--assignee", "a", "--assignee", "b"])
        exc = caught.value
        assert exc.code == "duplicate_option"
        assert "只能给一次" in exc.message, f"没说清标量只能给一次：{exc.message!r}"
        assert "--assignee a,b" not in exc.message


# --------------------------------------------------------------------------- #
# ④ D1：布尔开关末次显式值生效（#218 判据盲区的解析层直钉）
# --------------------------------------------------------------------------- #


class TestBooleanLastExplicitValue:
    def test_parse_layer_last_explicit_value_wins(self) -> None:
        """`--open_only --open_only=false` ⇒ 解析层聚合后值段是 'false'，转换层得 False。

        #218 原判据只断言 `success is True`、没读回值（A/B/C 全同集时判据恒真）。
        这里在解析/转换层直钉「末次显式值」：变异「首次值生效」会让本条得 True ⇒ 红。
        """
        spec = consoleRegistry.get_table().by_path["task ls"]
        _pos, opts, _src = cp._parse_options(
            spec, ["--assignee", "X", "--open_only", "--open_only=false"]
        )
        assert opts["open_only"] is False, (
            f"布尔重复应把末次显式值落下（解析层即转布尔），实际 {opts['open_only']!r}"
        )

    def test_bare_flag_is_true_and_single_false_is_false(self) -> None:
        """单向：裸开关 ⇒ True；单次显式 false ⇒ False（同集没分开时的常红/恒绿防护）。"""
        spec = consoleRegistry.get_table().by_path["task ls"]
        _pos, opts, _src = cp._parse_options(spec, ["--assignee", "X", "--open_only"])
        assert opts["open_only"] is True
        _pos, opts, _src = cp._parse_options(spec, ["--assignee", "X", "--open_only=false"])
        assert opts["open_only"] is False
