"""#228 判据债补强：#219 验收记档 D1–D4 的四条机器判据（单元层）。

出处：`agent/togo2_test/verify-219.md` §三（记档原文）+ 38 的 10 项变异表：
M6（去重）/ M8（空数组折 None）/ M9（值段 strip）/ M10（JSON 元素 trim）当时
23 条既有用例全绿——**行为正确但零判据**，本单只补判据、不改实现语义。

四条各自的「红在目标条」设计（互不共享触发条件，一个变异只红自己那类）：
  D1 去重保序：断言**重复值段/重复 JSON 元素被吞**（M6 去掉 dict.fromkeys 才红；
     无重复的 D2/D3/D4 用例不受影响）；
  D2 空数组→None：解析/转换层直钉 `[]`/`""`/`"  "` → None（M8 去掉 `or None` 才红；
     `clear_fields=[] ≡ 未提供` 是敏感语义，写反成「清空全部」）；
  D3 值段 strip：断言**前导/尾随空白的 `[` 段仍走 JSON**（M9 去掉 strip 后退回
     CSV 拆坏路径才红）；
  D4 JSON 不 trim：断言 JSON 元素**保留内部空格**、与 CSV 段 trim 成对照
     （M10 给元素加 strip 才红）。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleTool as ct


def _convert_array(raw, *, name: str = "status", command: str = "task ls"):
    return ct._convert_option(raw, kind="array", name=name, command=command)


# --------------------------------------------------------------------------- #
# D1（M6）：去重保序
# --------------------------------------------------------------------------- #


class TestD1DedupKeepsOrder:
    def test_repeated_segments_dedup(self) -> None:
        """重复值段（`--status DONE --status DONE` 聚合成 ['DONE','DONE']）被吞成 ['DONE']。"""
        assert ct._split_option_values(["DONE", "DONE"], name="status", command="task ls") == ["DONE"]
        assert ct._split_option_values(["DONE", "TODO", "DONE"], name="status", command="task ls") == ["DONE", "TODO"]

    def test_repeated_json_elements_dedup(self) -> None:
        """JSON 字面量内部重复元素同样被吞（`["a","a","b"]` → ["a","b"]）。"""
        assert _convert_array('["a","a","b"]') == ["a", "b"]

    def test_dedup_keeps_first_occurrence_order(self) -> None:
        """去重**保序**：首次出现位置决定顺序（["b","a","b"] → ["b","a"]，不是排序）。"""
        assert ct._split_option_values(["b", "a", "b"], name="status", command="task ls") == ["b", "a"]
        assert _convert_array('["b","a","b"]') == ["b", "a"]

    def test_mixed_csv_json_repeats_dedup(self) -> None:
        """CSV 与 JSON 混写的重复项同一张去重表（['a','b'] + '["b","c"]' → [a,b,c]）。"""
        assert ct._split_option_values(["a,b", '["b","c"]'], name="status", command="task ls") == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# D2（M8）：空数组 → None
# --------------------------------------------------------------------------- #


class TestD2EmptyValueSplit:
    """空值分流：**显式 `'[]'` → `[]`**、**纯空白 → `None`**（need_fix_4 §七 模块 1）。

    原判据把两者一并钉成 `None`（当时只有 `or None` 一条实现），代价是 CLI 无法表达
    「清空依赖」（`--depends-on '[]'` 被折成未提供 → `no_update_action`，need_fix_4 B3）。
    契约变更后两条各自成立：`[]` 是**显式空数组**，`None` 是**没提供**。
    `clear_fields=[] ≡ 未提供` 的那层语义没丢——它由 `tools.py` 的
    `_is_blank_update_value([])` 负责，与本层分流正交（集成用例里钉住）。
    """

    def test_empty_json_array_is_empty_list(self) -> None:
        """`'[]'`（显式空 JSON 数组）→ `[]`：这是「清空 / 一个都不要」，不是未提供。"""
        assert _convert_array("[]") == []
        assert _convert_array(" [] ") == []

    def test_empty_segment_is_none(self) -> None:
        """空值段与全空白值段同折 None（`--clear-fields ''` ≡ 未提供）。"""
        assert _convert_array("") is None
        assert _convert_array("   ") is None
        assert _convert_array(None) is None

    def test_repeated_empty_segments_stay_none(self) -> None:
        """空段重复出现（#218 聚合后是 ['','']）仍折 None，不许冒出一个空列表。"""
        assert _convert_array(["", "  "]) is None

    def test_explicit_empty_with_other_segments_is_not_empty(self) -> None:
        """`'[]'` 与其他值混写时按并集走：`['[]', '42']` → [42]，不因空数组整体变空。"""
        prop = {"type": "array", "items": {"type": "integer"}}
        assert (
            ct._convert_option(
                ["[]", "42"], kind="array", name="depends-on", command="task update", prop=prop
            )
            == [42]
        )

    def test_sensitive_field_variant(self) -> None:
        """`clear_fields` 的显式 `'[]'` 也返回 `[]`；「视为未提供」在工具层（见集成用例）。"""
        converted = ct._convert_option("[]", kind="array", name="clear_fields", command="task update")
        assert converted == []


# --------------------------------------------------------------------------- #
# D3（M9）：值段 strip
# --------------------------------------------------------------------------- #


class TestD3SegmentStrip:
    def test_leading_trailing_space_json_still_json(self) -> None:
        """`' ["a","b"] '`（两侧空白）strip 后仍按 JSON 解析，不退回 CSV 拆坏路径。"""
        assert ct._split_option_values(' ["a","b"] ', name="status", command="task ls") == ["a", "b"]

    def test_leading_space_bracket_segment(self) -> None:
        """verify-219 记档的原始事故形态：前导空格的 `[` 段。"""
        assert ct._split_option_values(' ["a","b"]', name="status", command="task ls") == ["a", "b"]

    def test_strip_applies_per_segment_not_only_first(self) -> None:
        """#218 聚合的**每个值段**各自 strip（第二个段带空白也要命中 JSON）。"""
        assert ct._split_option_values(["a", ' ["c","d"]'], name="status", command="task ls") == ["a", "c", "d"]

    def test_whitespace_only_segment_is_dropped(self) -> None:
        """纯空白值段被丢弃（不产出空串元素）。"""
        assert ct._split_option_values(["  ", '["a"]'], name="status", command="task ls") == ["a"]


# --------------------------------------------------------------------------- #
# D4（M10）：JSON 元素不 trim
# --------------------------------------------------------------------------- #


class TestD4JsonNoTrim:
    def test_json_element_keeps_inner_spaces(self) -> None:
        """`[" a ","b"]` → [" a ","b"]：JSON 是精确格式，元素不 trim。"""
        assert _convert_array('[" a ","b"]') == [" a ", "b"]

    def test_csv_segment_trims_in_contrast(self) -> None:
        """对照面：CSV 段**才** trim（`" a , b "` → ["a","b"]）——两种形态的差异是声明的口径。"""
        assert ct._split_option_values(" a , b ", name="status", command="task ls") == ["a", "b"]

    def test_json_element_spaces_reach_the_tool(self) -> None:
        """空格元素原样进转发参数（`--member-names '[" alice ","bob"]'` → [' alice ','bob']）。

        下游（save_room 按成员名查库）会因 `' alice '` 查无此人而报错——报错里
        出现的就是**带空格**的原名：这是「不 trim」的行为级证据（集成层复钉）。
        """
        assert _convert_array('[" alice ","bob"]', name="member_names", command="room create") == [
            " alice ",
            "bob",
        ]

    def test_json_element_no_strip_at_ends(self) -> None:
        """尾随空格同样保留（strip 是双向的，钉住另一侧）。"""
        assert _convert_array('["a ","b"]') == ["a ", "b"]
