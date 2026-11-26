"""#375 模块 2/4 的单元判据：help 单一返回字段、枚举防截断、说明不腰斩、资源列表派生。

为什么这些判据要在**单元层**再钉一遍（集成层另有一份真入口的）：
1. `help` 的返回形态是**契约**（`{"success": True, "text": ...}`），三处产出点
   （`consoleTool` / `consoleMcp` / `consoleSpec`）很容易有一处漏改，而集成层只走
   模型常用的那几条分支；
2. 枚举与截断是**纯函数**行为（`_extract_enum` / `_enum_note` / `_first_clause`），
   单元层才能把「为什么这一行会缺值」定位到具体函数——集成层红了只能看到整段 help。

对应 need_fix_4 §七 模块 2/4 与 §三.1/.2/.4/.5。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct


# --------------------------------------------------------------------------- #
# 1. help 单一返回字段（`text`；不许 `help`、不许双写）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tokens",
    [
        ["help"],
        ["help", "task"],
        ["help", "task", "create"],
        ["help", "mcp"],
        ["help", "openapi"],
        ["help", "graphql"],
    ],
)
def test_help_payload_has_text_only(tokens: list[str]) -> None:
    """每条 help 分支只有一个正文字段 `text`（need_fix_4 §六：双写会让 token 翻倍）。"""
    body = ct._help_text(tokens)
    assert body["success"] is True
    assert set(body) == {"success", "text"}, body.keys()
    assert isinstance(body["text"], str) and body["text"].strip()


# --------------------------------------------------------------------------- #
# 2. 枚举提取：顶层 enum / items.enum / anyOf 分支
# --------------------------------------------------------------------------- #


class TestExtractEnum:
    def test_top_level_enum(self) -> None:
        prop = {"type": "string", "enum": ["HIGH", "NORMAL", "LOW"]}
        assert cs._extract_enum(prop) == ["HIGH", "NORMAL", "LOW"]

    def test_items_enum(self) -> None:
        """数组参数的枚举挂在 `items` 下（`--status a,b` 这类多值枚举）。"""
        prop = {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}
        assert cs._extract_enum(prop) == ["a", "b"]

    def test_anyof_branches_merge_without_null(self) -> None:
        """`Optional[Literal[...]]` 渲染成 anyOf，`null` 分支不带 enum，天然被跳过。"""
        prop = {
            "anyOf": [
                {"type": "string", "enum": ["advance", "reject"]},
                {"type": "null"},
            ]
        }
        assert cs._extract_enum(prop) == ["advance", "reject"]

    def test_no_enum_returns_empty(self) -> None:
        assert cs._extract_enum({"type": "string"}) == []

    def test_duplicate_values_are_deduped_in_declaration_order(self) -> None:
        prop = {"anyOf": [{"enum": ["a", "b"]}, {"enum": ["b", "c"]}]}
        assert cs._extract_enum(prop) == ["a", "b", "c"]


class TestEnumNote:
    def test_appended_when_values_missing(self) -> None:
        prop = {"type": "string", "enum": ["advance", "reject"]}
        line = cs.describe_param("workflow_action", prop, required=False)
        assert "（可选值：advance | reject）" in line, line

    def test_skipped_when_all_values_already_visible(self) -> None:
        """说明文字里已经完整写了枚举 ⇒ 不再追加一遍（同一行不印两次）。"""
        prop = {
            "type": "string",
            "enum": ["HIGH", "NORMAL", "LOW"],
            "description": "优先级，HIGH / NORMAL / LOW，默认 NORMAL",
        }
        line = cs.describe_param("priority", prop, required=False)
        assert "可选值：" not in line, line
        for value in ("HIGH", "NORMAL", "LOW"):
            assert value in line, line

    def test_positional_line_also_gets_enum(self) -> None:
        prop = {"type": "string", "enum": ["a", "b"]}
        line = cs.describe_positional("<mode>", prop, required=True)
        assert "（可选值：a | b）" in line, line


# --------------------------------------------------------------------------- #
# 3. 说明不腰斩：只按 `。` 切首句 + 上限 120
# --------------------------------------------------------------------------- #


class TestFirstClause:
    def test_does_not_cut_at_comma_or_semicolon(self) -> None:
        """`，` / `；` 是句内标点，切在它们上面就是 §三.2 的腰斩现场。"""
        text = "修改任务依赖（**三态**）：①省略 = 不改；②显式 [] = 清空；③传列表 = 替换。后面这句不要。"
        clause = cs._first_clause(text)
        assert clause.endswith("替换")
        assert "③传列表 = 替换" in clause
        assert "后面这句不要" not in clause

    def test_keeps_enum_inside_first_sentence(self) -> None:
        """7 个状态值都在第一句里，历史实现在第一个逗号处把它们全丢了。"""
        text = (
            "目标状态，可选 TODO、PENDING、IN_PROGRESS、ON_HOLD、REVIEWING、DONE、CANCELLED；"
            "省略或等于当前状态表示追加评论。"
        )
        clause = cs._first_clause(text)
        for status in ("TODO", "PENDING", "IN_PROGRESS", "ON_HOLD", "REVIEWING", "DONE", "CANCELLED"):
            assert status in clause, clause

    def test_upper_bound_is_120(self) -> None:
        assert cs._DESCRIPTION_MAX == 120
        long_text = "甲" * 200 + "。"
        clause = cs._first_clause(long_text)
        assert clause.endswith("…")
        assert len(clause) == cs._DESCRIPTION_MAX + 1  # 120 字 + 省略号

    def test_short_sentence_untouched(self) -> None:
        assert cs._first_clause("任务标题。第二句") == "任务标题"


# --------------------------------------------------------------------------- #
# 4. 真实工具的两条枚举在 help 行里可读（schema 侧，不经命令表）
# --------------------------------------------------------------------------- #


def test_priority_enum_visible_in_describe_param() -> None:
    prop = cs.param_schemas("create_task")["priority"]
    line = cs.describe_param("priority", prop, required=False)
    for value in ("HIGH", "NORMAL", "LOW"):
        assert value in line, line


def test_new_status_enum_visible_in_describe_param() -> None:
    prop = cs.param_schemas("update_task")["new_status"]
    line = cs.describe_param("new_status", prop, required=False)
    for status in ("TODO", "PENDING", "IN_PROGRESS", "ON_HOLD", "REVIEWING", "DONE", "CANCELLED"):
        assert status in line, line


# --------------------------------------------------------------------------- #
# 5. `help <资源>` 从命令表派生（每子命令一行 + 详情行）
# --------------------------------------------------------------------------- #


def test_resource_help_lists_each_command_with_purpose() -> None:
    text = ct._help_text(["help", "task"])["text"]
    lines = [line for line in text.splitlines() if line.startswith("  ")]
    assert len(lines) >= 4, text
    assert all(" — " in line for line in lines), text
    assert any(line.strip().startswith("task create") for line in lines), text
    assert "详情：help task <操作>" in text, text


def test_summary_has_locator_rule() -> None:
    text = ct._help_text(["help"])["text"]
    assert "定位规则：" in text, text
    assert "agent dm|wake 与 role 用 <名称>" in text, text


def test_agent_get_help_has_no_name_lookup_contradiction() -> None:
    """`agent get` 既说「不接受按名称定位」又说「按名字查询走运行时列表」就是自相矛盾。"""
    text = ct._help_text(["help", "agent", "get"])["text"]
    assert "按名字查询走运行时列表" not in text, text
    assert "定位只有位置参数 <id>" in text, text
