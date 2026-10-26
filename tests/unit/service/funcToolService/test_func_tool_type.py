"""#72：tool schema 解析器（`get_function_metadata`）鲁棒性护栏。

背景：旧实现用 `docstring.split("Args:")` + 「段内首个空行截断」+ 「只取首行当 description」，
导致 5 类写法静默失效（线上真实案例：`finish_action.confirm_no_need_talk` 的三条规则模型完全看不到，
只看到一个裸 boolean）。本文件用内联假函数固化这 5 类写法，并加一个**全量 lint**：
以后任何人写「参数：」、`Arguments:`、漏写参数说明或首行过短，都会直接红。
"""

from __future__ import annotations

import inspect

import pytest

from service.funcToolService import tools
from service.funcToolService.funcToolType import get_function_metadata
from service.agentService.toolRegistry import CATEGORY_CONFIG


def _props(func, name: str = "fake") -> dict:
    """取某函数的 parameters.properties（省掉重复样板）。"""
    return get_function_metadata(name, func)["parameters"]["properties"]


# ---------------------------------------------------------------------------
# 类 1：用「参数：」/「参数说明：」当段标题（全角冒号 + 中文标题）
# ---------------------------------------------------------------------------


def test_chinese_args_title_is_recognized():
    """「参数：」当段标题时必须解析出参数说明（旧实现 0 条）。"""

    def fake(a: int, b: str = "x") -> dict:
        """示例工具说明（中文参数段标题）。

        参数：
            a: 第一个参数的说明
            b: 第二个参数的说明
        """
        return {}

    props = _props(fake)
    assert props["a"]["description"] == "第一个参数的说明"
    assert props["b"]["description"] == "第二个参数的说明"


def test_chinese_args_title_with_fullwidth_colon_and_bullets():
    """「参数说明：」+ 全角冒号 + 列表符号 / 类型括号，都要能解析。"""

    def fake(flag: bool = False) -> dict:
        """示例工具说明（参数说明 + 列表符号）。

        参数说明：
            - flag (bool)：是否置位（默认 false），
              多条规则写在这里。
        """
        return {}

    desc = _props(fake)["flag"]["description"]
    assert desc.startswith("是否置位（默认 false）")
    assert "多条规则写在这里" in desc  # 续行要拼进来，不能丢


# ---------------------------------------------------------------------------
# 类 2：正文提到 `Args:` 字面串 —— 不得劫持参数段
# ---------------------------------------------------------------------------


def test_args_literal_in_prose_does_not_hijack_section():
    """正文出现 `Args:` 字面串时，真正的参数段仍要被解析（旧实现会被截断）。"""

    def fake(a: int) -> dict:
        """示例工具说明（正文提到 Args: 字面串）。

        注意：正文里提到 Args: 这种字面串时，旧解析会把第一处当作段头。

        Args:
            a: 真正的参数说明
        """
        return {}

    assert _props(fake)["a"]["description"] == "真正的参数说明"


# ---------------------------------------------------------------------------
# 类 3：Args 段内出现空行 —— 空行之后的参数不得丢失
# ---------------------------------------------------------------------------


def test_blank_line_inside_args_section_does_not_terminate():
    """段内空行不终止参数段（旧实现 `split("\\n\\n")` 会把后面参数全丢掉）。"""

    def fake(a: int, b: int = 2) -> dict:
        """示例工具说明（段内空行）。

        Args:
            a: 空行之前的参数

            b: 空行之后的参数
        """
        return {}

    props = _props(fake)
    assert props["a"]["description"] == "空行之前的参数"
    assert props["b"]["description"] == "空行之后的参数"


# ---------------------------------------------------------------------------
# 类 4：`Arguments:` / `Parameters:` 段标题
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", ["Arguments:", "Parameters:", "arguments：", "Parameters ："])
def test_alternative_args_titles(title: str):
    """`Arguments:` / `Parameters:`（含全角冒号）都要认。"""

    def fake(query: str) -> dict:
        return {}

    # docstring 需参数化，故用赋值方式注入（等价于写在函数体首行）。
    fake.__doc__ = (
        "示例工具说明（别名段标题）。\n"
        "\n"
        f"{title}\n"
        "    query: 查询关键词\n"
    )

    assert _props(fake)["query"]["description"] == "查询关键词"


# ---------------------------------------------------------------------------
# 类 5：全角冒号 / 全角括号
# ---------------------------------------------------------------------------


def test_fullwidth_colon_and_parens():
    """`a （必填）：说明` 这种全角写法必须解析（旧实现只按半角 `:` 切）。"""

    def fake(a: str, b: int = 1) -> dict:
        """示例工具说明（全角冒号与括号）。

        Args:
            a （必填）：甲参数的说明
            b（可选）：乙参数的说明
        """
        return {}

    props = _props(fake)
    assert props["a"]["description"] == "甲参数的说明"
    assert props["b"]["description"] == "乙参数的说明"


# ---------------------------------------------------------------------------
# 基础规则回归：续行、description 首段、段结束、私有参数、required
# ---------------------------------------------------------------------------


def test_continuation_lines_are_joined_into_previous_param():
    """不匹配参数行的段内内容要拼到上一个参数说明（空格连接），不能丢弃。"""

    def fake(a: str) -> dict:
        """示例工具说明。

        Args:
            a: 第一句说明。
                第二句续行说明。
        """
        return {}

    assert _props(fake)["a"]["description"] == "第一句说明。 第二句续行说明。"


def test_description_uses_whole_first_paragraph():
    """description 取首段**原文**（保留段内换行，不拼接），不再只取首行。"""

    def fake(a: str) -> dict:
        """首段第一句说明。
        首段第二句说明。

        Args:
            a: 参数说明
        """
        return {}

    meta = get_function_metadata("fake", fake)
    assert meta["description"] == "首段第一句说明。\n首段第二句说明。"


def test_description_excludes_sections_after_args():
    """description 只取首段：`Args:` 之后的 Returns / Notes 一律不得混进来。

    边界（Operator 拍板）：description 只放首段，避免把 docstring 全文塞进 schema
    （实测 26 个工具会从 ~800 字符膨胀到 ~6000 字符，每次请求都要跟着发）。
    """

    def fake(a: str) -> dict:
        """首段说明（只这一句）。

        Args:
            a: 参数说明

        Returns:
            这里的内容不该出现在 description 里
        """
        return {}

    meta = get_function_metadata("fake", fake)
    assert meta["description"] == "首段说明（只这一句）。"
    assert "不该出现" not in meta["description"]
    assert "参数说明" not in meta["description"]


def test_section_ends_at_next_section_title():
    """遇下一个段标题（Returns:）参数段结束，不得把 Return 内容当成续行。"""

    def fake(a: str) -> dict:
        """示例工具说明。

        Args:
            a: 参数说明

        Returns:
            b: 这不是参数
        """
        return {}

    props = _props(fake)
    assert props["a"]["description"] == "参数说明"
    assert "b" not in props  # 签名里没有 b，自然不暴露
    assert "这不是参数" not in props["a"]["description"]


def test_private_params_skipped_and_required_computed():
    """`_` 前缀参数不进 schema；无默认值者进 required。"""

    def fake(a: int, b: str = "x", _context=None) -> dict:
        """示例工具说明。

        Args:
            a: 必填参数
            b: 可选参数
        """
        return {}

    meta = get_function_metadata("fake", fake)
    props = meta["parameters"]["properties"]
    assert set(props) == {"a", "b"}
    assert meta["parameters"]["required"] == ["a"]


def test_bullet_prefixed_param_line_is_parsed():
    """参数行前导列表符号（`- `）兼容。"""

    def fake(a: int) -> dict:
        """示例工具说明。

        Args:
            - a: 带项目符号的参数说明
        """
        return {}

    assert _props(fake)["a"]["description"] == "带项目符号的参数说明"


# ---------------------------------------------------------------------------
# 全量 lint：所有已注册工具都必须有「参数说明」+「有意义的首行」
# ---------------------------------------------------------------------------


def _iter_local_tools():
    """遍历 CATEGORY_CONFIG 中确实存在于 tools.py 的工具（TSP 工具不在此列）。"""
    for tool_name in CATEGORY_CONFIG:
        func = getattr(tools, tool_name, None)
        if func is not None:
            yield tool_name, func


def test_all_local_tools_expose_param_descriptions_and_meaningful_description():
    """全量 lint：每个非私有参数都要有非空说明，且 description 首行 ≥ 8 字符。

    这条用例是本单的护栏：以后谁把参数段写成「参数：」/`Arguments:` 以外的怪写法、
    或漏写参数说明、或首行只剩两三个字，CI 直接红。
    """
    problems: list[str] = []

    for tool_name, func in _iter_local_tools():
        meta = get_function_metadata(tool_name, func)
        props = meta["parameters"]["properties"]

        for param_name in inspect.signature(func).parameters:
            if param_name == "self" or param_name.startswith("_"):
                continue
            desc = (props.get(param_name) or {}).get("description") or ""
            if not desc.strip():
                problems.append(f"{tool_name}.{param_name}: 缺参数说明（schema 里模型看不到）")

        first_line = (meta["description"] or "").strip().split("\n")[0]
        if len(first_line) < 8:
            problems.append(f"{tool_name}: description 首行过短（{first_line!r}）")

    assert not problems, "tool schema 存在以下问题：\n" + "\n".join(problems)


def test_finish_action_confirm_no_need_talk_description_is_visible():
    """线上真实案例回归：`finish_action.confirm_no_need_talk` 的三条规则必须进 schema。"""
    props = _props(tools.finish_action, "finish_action")
    desc = props["confirm_no_need_talk"]["description"]
    assert desc
    assert "true" in desc
    assert "send_chat_msg" in desc  # ⚠️ 提示必须可见
