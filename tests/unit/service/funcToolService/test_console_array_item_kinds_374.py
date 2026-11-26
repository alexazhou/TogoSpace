"""need_fix_4 模块 1 的单元判据：数组元素类型提取、形态注与示例同源、按元素类型收敛。

分工说明（与集成用例互补，不重复）：
- 本文件钉**判定逻辑**：`array_item_kinds` 三种形态、`array_shape_and_example` 的形态注
  与示例必须成对、`_convert_option` 的空值分流与元素校验。
- `tests/integration/.../test_console_task.py::TestConsoleArrayCliNeedFix4` 钉**真入口**：
  console → registry → 第 2 跳 → DB。B1 的失败点在第 2 跳 schema 校验，只测本文件看不到
  那条链路，所以两边都要有。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct
from service.funcToolService.consoleParsing import ConsoleError

DEPENDS_ON = cs.param_schemas("create_task")["depends_on"]
STAGES = cs.param_schemas("create_task")["stages"]
MEMBER_NAMES = cs.param_schemas("create_dept")["member_names"]


# --------------------------------------------------------------------------- #
# 元素类型提取
# --------------------------------------------------------------------------- #


class TestArrayItemKinds:
    def test_integer_array(self) -> None:
        """`depends_on`：`items.type = integer` ⇒ `{integer}`。"""
        assert cs.array_item_kinds(DEPENDS_ON) == frozenset({"integer"})

    def test_object_or_integer_array(self) -> None:
        """`stages`：`items.anyOf = [object, integer]` ⇒ `{object, integer}`。"""
        assert cs.array_item_kinds(STAGES) == frozenset({"object", "integer"})

    def test_string_array(self) -> None:
        """`member_names`：`items.type = string` ⇒ `{string}`。"""
        assert cs.array_item_kinds(MEMBER_NAMES) == frozenset({"string"})

    def test_fallback_is_string_when_items_missing(self) -> None:
        """没有 `items` 时兜底 `{string}`（与 `param_kind` 的兜底同思路，不瞎猜整数）。"""
        assert cs.array_item_kinds({"type": "array"}) == frozenset({"string"})

    def test_null_is_dropped(self) -> None:
        """`items.type` 里的 `null` 被丢掉：元素类型只谈非空的那支。"""
        assert cs.array_item_kinds({"type": "array", "items": {"type": ["string", "null"]}}) == frozenset({"string"})
        assert cs.array_item_kinds(
            {"type": "array", "items": {"anyOf": [{"type": "null"}, {"type": "integer"}]}}
        ) == frozenset({"integer"})

    def test_top_level_param_kind_still_array(self) -> None:
        """顶层 `param_kind` 仍返回 `array`——元素类型不污染解析层的重复选项聚合。"""
        assert cs.param_kind(DEPENDS_ON) == "array"
        assert cs.param_kind(STAGES) == "array"


# --------------------------------------------------------------------------- #
# 形态注与示例同源（need_fix_4 §三.3 + B2）
# --------------------------------------------------------------------------- #


class TestShapeAndExample:
    def test_integer_shape_and_example(self) -> None:
        note, example = cs.array_shape_and_example(frozenset({"integer"}))
        assert "CSV（1,2）" in note and "[1,2]" in note
        assert example == "[1,2]"

    def test_string_shape_and_example(self) -> None:
        note, example = cs.array_shape_and_example(frozenset({"string"}))
        assert "CSV（a,b）" in note
        assert example == '["a","b"]'

    def test_object_shape_and_example(self) -> None:
        note, example = cs.array_shape_and_example(frozenset({"object", "integer"}))
        assert "owner_id" in note
        assert example == '[{"owner_id":7}]'

    def test_describe_param_uses_item_kinds(self) -> None:
        """三种元素形态各出一条 help 行断言：形态注跟着元素类型走（§三.3 的误导文案消失）。"""
        integer_line = cs.describe_param("depends_on", DEPENDS_ON, required=False)
        object_line = cs.describe_param("stages", STAGES, required=False)
        string_line = cs.describe_param("member_names", MEMBER_NAMES, required=False)

        assert "[1,2]" in integer_line, integer_line
        assert '"a","b"' not in integer_line, integer_line
        assert "owner_id" in object_line, object_line
        assert '"a","b"' in string_line, string_line

    def test_help_line_and_error_example_are_same_source(self) -> None:
        """help 里写的示例字面量，与报错里让人照抄的示例，必须是同一个串（B2 的根因）。"""
        prop = DEPENDS_ON
        with pytest.raises(ConsoleError) as excinfo:
            ct._convert_option("[a,b]", kind="array", name="depends-on", command="task create", prop=prop)
        example = excinfo.value.extra["example"]
        literal = example.split("--depends-on ", 1)[1].strip("'")
        assert f"（{literal!r}" or literal in cs.describe_param("depends_on", prop, required=False)
        assert literal in cs.describe_param("depends_on", prop, required=False), (
            f"help 与报错示例不同源：help={cs.describe_param('depends_on', prop, required=False)!r} "
            f"example_literal={literal!r}"
        )


# --------------------------------------------------------------------------- #
# 按元素类型收敛
# --------------------------------------------------------------------------- #


class TestIntegerArrayConversion:
    def test_csv_converts_to_int(self) -> None:
        assert ct._convert_option("42,44", kind="array", name="depends-on", command="c", prop=DEPENDS_ON) == [42, 44]

    def test_repeated_segments_convert_to_int(self) -> None:
        assert ct._convert_option(["42", "44"], kind="array", name="depends-on", command="c", prop=DEPENDS_ON) == [42, 44]

    def test_json_integer_array_accepted(self) -> None:
        assert ct._convert_option("[42,44]", kind="array", name="depends-on", command="c", prop=DEPENDS_ON) == [42, 44]

    def test_csv_dedup_keeps_order(self) -> None:
        assert ct._convert_option("42,44,42", kind="array", name="depends-on", command="c", prop=DEPENDS_ON) == [42, 44]

    def test_non_positive_rejected(self) -> None:
        with pytest.raises(ConsoleError) as excinfo:
            ct._convert_option("[0]", kind="array", name="depends-on", command="c", prop=DEPENDS_ON)
        assert excinfo.value.code == "invalid_option_value"

    def test_non_numeric_csv_rejected(self) -> None:
        with pytest.raises(ConsoleError) as excinfo:
            ct._convert_option("a,b", kind="array", name="depends-on", command="c", prop=DEPENDS_ON)
        assert excinfo.value.code == "invalid_option_value"

    def test_string_element_in_integer_array_rejected(self) -> None:
        with pytest.raises(ConsoleError) as excinfo:
            ct._convert_option('["42"]', kind="array", name="depends-on", command="c", prop=DEPENDS_ON)
        assert excinfo.value.code == "invalid_option_value"


class TestBooleanElementRejected:
    def test_bool_element_rejected(self) -> None:
        """`True` 是 `int` 的子类，放过去就变成 id `1`——必须显式拦。"""
        with pytest.raises(ConsoleError) as excinfo:
            ct._convert_option("[true]", kind="array", name="depends-on", command="c", prop=DEPENDS_ON)
        assert "布尔" in excinfo.value.message


class TestObjectArrayConversion:
    def test_json_object_array_accepted(self) -> None:
        assert ct._convert_option(
            '[{"owner_id":7}]', kind="array", name="stages", command="c", prop=STAGES
        ) == [{"owner_id": 7}]

    def test_json_integer_array_accepted_for_object_or_integer(self) -> None:
        assert ct._convert_option("[7,9]", kind="array", name="stages", command="c", prop=STAGES) == [7, 9]

    def test_csv_integer_accepted_for_object_or_integer(self) -> None:
        assert ct._convert_option("7,9", kind="array", name="stages", command="c", prop=STAGES) == [7, 9]

    def test_no_dedup_when_object_involved(self) -> None:
        """含 `object` 时不去重：`[7,8,7]` 是合法工作流（同一成员跨节点复审）。"""
        assert ct._convert_option("[7,8,7]", kind="array", name="stages", command="c", prop=STAGES) == [7, 8, 7]

    def test_dict_element_is_hashable_safe(self) -> None:
        """`dict` 元素不再喂给 `dict.fromkeys`（原实现会抛 `unhashable type: 'dict'`）。"""
        got = ct._convert_option(
            '[{"owner_id":7},{"owner_id":9}]', kind="array", name="stages", command="c", prop=STAGES
        )
        assert got == [{"owner_id": 7}, {"owner_id": 9}]

    def test_object_only_array_rejects_csv(self) -> None:
        """纯 object 的无 CSV 写法：给出 JSON 数组指引而不是硬拆。"""
        prop = {"type": "array", "items": {"type": "object"}}
        with pytest.raises(ConsoleError) as excinfo:
            ct._convert_option("7,9", kind="array", name="stages", command="c", prop=prop)
        assert excinfo.value.code == "invalid_option_value"
        assert "JSON 数组" in excinfo.value.message


class TestEmptyValueSplit:
    def test_explicit_empty_json_array_is_empty_list(self) -> None:
        assert ct._convert_option("[]", kind="array", name="depends-on", command="c", prop=DEPENDS_ON) == []

    def test_blank_segment_is_none(self) -> None:
        assert ct._convert_option("", kind="array", name="status", command="c", prop=MEMBER_NAMES) is None
        assert ct._convert_option("   ", kind="array", name="status", command="c", prop=MEMBER_NAMES) is None
        assert ct._convert_option(None, kind="array", name="status", command="c", prop=MEMBER_NAMES) is None

    def test_prop_absent_falls_back_to_string(self) -> None:
        """没传 `prop` 时按 `string` 收（历史内部调用面不变）。"""
        assert ct._convert_option("a,b", kind="array", name="member-names", command="c") == ["a", "b"]
