"""#72：tool schema 解析器（`get_function_metadata`）鲁棒性护栏。

背景：旧实现用 `docstring.split("Args:")` + 「段内首个空行截断」+ 「只取首行当 description」，
导致 5 类写法静默失效（线上真实案例：`finish_action.confirm_no_need_talk` 的三条规则模型完全看不到，
只看到一个裸 boolean）。本文件用内联假函数固化这 5 类写法，并加一个**全量 lint**：
以后任何人写「参数：」、`Arguments:`、漏写参数说明或首行过短，都会直接红。
"""

from __future__ import annotations

import inspect
from typing import Optional, Union

import pytest

from service.funcToolService import tools
from service.funcToolService.funcToolType import (
    _ARGS_SECTION_TITLES,
    _PARAM_LINE_PATTERN,
    _match_section_title,
    _parse_param_descriptions,
    _strip_bullet,
    get_function_metadata,
    python_type_to_json_schema,
)
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
# 类 6：冒名参数行（`NOTE:` / `TODO:` 等英文标识符 + 冒号，但不是签名参数）
# ---------------------------------------------------------------------------


def _valid_param_names(func) -> set[str]:
    """与 `get_function_metadata` 同口径的「合法参数名」集合。"""
    return {
        name for name in inspect.signature(func).parameters
        if name != "self" and not name.startswith("_")
    }


def test_misnamed_param_like_line_is_continuation_not_ghost_key():
    """`NOTE:` 不是签名参数 ⇒ 当续行拼给上一个参数，且它自己的正文不得被丢掉。

    旧行为：`NOTE` 被当成参数名顶替 `current_param`，于是
    ① 上一参数的后续续行被它抢走；② `NOTE` 正文存进幽灵键后被签名过滤 ⇒ 静默消失。
    """

    def fake(a: str, b: str = "") -> dict:
        """示例工具说明。

        Args:
            a: 甲参数说明
                NOTE: 甲的重要补充
            b: 乙参数说明
        """
        return {}

    props = _props(fake)
    assert props["a"]["description"] == "甲参数说明 NOTE: 甲的重要补充"
    assert props["b"]["description"] == "乙参数说明"


def test_orphan_continuation_at_section_start_does_not_crash():
    """段首就是无主续行（没有当前参数）⇒ 忽略、不抛错，后续参数照常解析。"""

    def fake(a: str) -> dict:
        """示例工具说明。

        Args:
            段首孤立的一行（不属于任何参数）
            a: 甲参数说明
        """
        return {}

    props = _props(fake)
    assert props["a"]["description"] == "甲参数说明"
    assert "段首孤立的一行" not in props["a"]["description"]


def test_parsed_param_keys_never_exceed_signature_params():
    """防幽灵键回潮：解析结果的 key 必须全部 ⊆ 签名里的合法参数名。"""

    def fake(a: str, b: str = "") -> dict:
        """示例工具说明。

        Args:
            a: 甲参数说明
                NOTE: 补充说明
                TODO: 待办说明
            b: 乙参数说明
        """
        return {}

    valid = _valid_param_names(fake)
    parsed = _parse_param_descriptions(inspect.getdoc(fake) or "", valid)

    assert set(parsed) <= valid
    assert "NOTE" not in parsed
    assert "TODO" not in parsed


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


# ---------------------------------------------------------------------------
# 反向 lint：`Args:` 段里「形如参数行但名字不在函数签名里」（拼错 / 幽灵参数）
# ---------------------------------------------------------------------------

# 允许出现在 `Args:` 段里、但不是参数的标签行（大小写不敏感）。
_ALLOWED_LABEL_NAMES = frozenset({"note", "notes", "todo", "example", "examples", "warning", "tip"})


def _iter_args_param_like_lines(func):
    """产出 `Args:` 段内所有「形如参数行」的 `(参数名, 原始行)`（不看是否在签名里）。"""
    docstring = inspect.getdoc(func) or ""
    in_args_section = False
    args_indent = 0

    for raw_line in docstring.split("\n"):
        stripped = raw_line.strip()

        if not in_args_section:
            if _match_section_title(raw_line) in _ARGS_SECTION_TITLES:
                in_args_section = True
                args_indent = len(raw_line) - len(raw_line.lstrip())
            continue

        if not stripped:
            continue

        matched = _PARAM_LINE_PATTERN.match(_strip_bullet(stripped))
        if matched:
            yield matched.group("name"), stripped
            continue

        if len(raw_line) - len(raw_line.lstrip()) <= args_indent:
            break  # 参数段结束（下一个段标题 / 缩进回退）


def test_no_misnamed_param_lines_in_tools_docstrings():
    """反向 lint：`Args:` 段里形如参数行、但名字不在签名里的行必须报出来。

    这类行（例如把 `task_id` 拼成 `taks_id`）会被解析器当成**续行**静默并进上一个
    参数，既不报错也不进 schema —— 拼错就永远发现不了。白名单放行
    `NOTE` / `TODO` / `Note` / `Example(s)` 等标签行（大小写不敏感）。
    """
    problems: list[str] = []
    scanned = 0

    for tool_name, func in _iter_local_tools():
        valid_names = _valid_param_names(func)
        for name, raw_line in _iter_args_param_like_lines(func):
            scanned += 1
            if name in valid_names or name.lower() in _ALLOWED_LABEL_NAMES:
                continue
            problems.append(f"{tool_name}: {raw_line!r} —— 签名里没有参数 {name!r}")

    # 防止用例空转：必须真的扫到了参数行
    assert scanned > 0, "反向 lint 没扫到任何参数行，用例可能空转"
    assert not problems, (
        "以下 `Args:` 段参数行不在函数签名里（疑似拼错或幽灵参数）：\n" + "\n".join(problems)
    )


# ---------------------------------------------------------------------------
# 多分支 Union：全局生成器**不**展开成 anyOf，而是回退成 {"type": "object"}
# ---------------------------------------------------------------------------

def test_multi_branch_union_expands_to_any_of():
    """多分支 Union 展开成 `anyOf`，`Optional[...]` 由 schema 自身表达 null。

    ## 旧裁定的作废依据（必须写明，否则下一个人会把这次改动当成违规回潮）

    Operator **2026-11-12 00:20** 授权（#195）：「ok，就按这个方案修改下，开一个任务，
    先 hold；后续我喊你再开始」。该授权**明确取代** #83 事件 215（2026-10-28）的旧裁定
    「`funcToolType.python_type_to_json_schema` 的 Union 多分支 → anyOf 改动必须回退、
    全局生成器保持零改动」。旧裁定的理由是「claude_sdk 下只支持列表，anyOf 不再是必要
    手段」；新裁定认为必须改生成器，因为 `Optional[T]` 被剥成 `T` 会让 19 个注解不含
    None 的字段接受 null（`create_task.description=null` 一路撞到
    `NOT NULL constraint failed`）。

    ## 本用例现在钉什么

    - 多分支 Union ⇒ `anyOf` 逐分支展开（不再是裸 `{"type": "object"}`）；
    - `Optional[...]` 包裹 ⇒ 结果里**必须出现 null 分支**（`type` 数组含 `"null"` 或
      `anyOf` 含 `{"type": "null"}`），且不能退化成 `object`；
    - 反向对照：`Optional[str]` 仍是 `type: ["string", "null"]` —— 扁平化不能把它
      变成 `object`，也不能反向把非 Optional 的 `str` 弄成可空。
    """

    def allows_null(schema: dict) -> bool:
        """schema 是否声明允许 null（两种合法表达都认）。"""
        declared = schema.get("type")
        if isinstance(declared, list) and "null" in declared:
            return True
        any_of = schema.get("anyOf")
        return isinstance(any_of, list) and {"type": "null"} in any_of

    # 裸多分支 Union（无 NoneType）：逐分支展开。
    assert python_type_to_json_schema(Union[str, list[str]]) == {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ]
    }
    # `A | B` 语法与 `typing.Union` 是同一件事，必须给同一个结果。
    assert python_type_to_json_schema(str | list[str]) == {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ]
    }

    # `Optional[Union[...]]` 是三分支：**不扁平化**，保留 anyOf 且含 null 分支。
    optional_union = python_type_to_json_schema(Optional[Union[str, list[str]]])
    assert allows_null(optional_union), optional_union
    assert optional_union.get("type") != "object", optional_union

    # 多分支（含 NoneType 的三分支以上）同样保留 anyOf 形态。
    three_branch = python_type_to_json_schema(Optional[Union[str, int, list[str]]])
    assert allows_null(three_branch), three_branch

    # 反向对照：单纯 `Optional[T]` 走扁平化 ⇒ `type: [T, "null"]`，不是 object、不是 anyOf。
    assert python_type_to_json_schema(Optional[str]) == {"type": ["string", "null"]}
    # 非 Optional 的标量不能变成可空（防扁平化把 null 泄漏到所有类型上）。
    assert python_type_to_json_schema(str) == {"type": "string"}


def _multi_branch_union_params() -> list[str]:
    """扫描所有本地工具，找出参数类型是「多分支 Union」的那些（正常应为空）。"""
    from typing import get_args, get_origin, get_type_hints
    from types import UnionType

    hits: list[str] = []
    for tool_name, func in _iter_local_tools():
        try:
            hints = get_type_hints(func)
        except Exception:  # noqa: BLE001 - 取不到类型提示就跳过，本用例只做检查
            continue
        for param_name, param_type in hints.items():
            if param_name.startswith("_"):
                continue
            if get_origin(param_type) not in (Union, UnionType):
                continue
            args = get_args(param_type)
            if len(args) == 2 and type(None) in args:
                continue  # `Optional[T]`：走剥 None 分支，不是多分支 Union
            hits.append(f"{tool_name}.{param_name}: {param_type}")
    return hits


def test_no_local_tool_uses_multi_branch_union_param():
    """全仓本地工具当前**没有**任何多分支 Union 参数 —— 这是上一条护栏的「为什么」。

    `python_type_to_json_schema` 的多分支 Union 分支现在只有上面那条用例覆盖，
    没有任何真实工具会走到它。所以这里立一个信号灯：**谁第一个给工具参数写了
    多分支 Union，这条就红**，提示他去看一眼生成器给出的 `anyOf` 形态是否就是他要的
    （给模型逐分支的类型信息，已不再是改造前的裸 `{"type": "object"}`）。

    红的时候不要直接把参数改掉绕过去：先按上一条用例的结论确认自己要的行为
    （`anyOf` 会逐分支展开，模型能看到每个分支的类型），再决定参数该不该这么写。
    多分支 Union 的形态已由 Operator 2026-11-12 00:20 授权（#195），**不是**违规回潮。
    """
    hits = _multi_branch_union_params()
    assert not hits, (
        "出现了首个多分支 Union 工具参数，生成器会对它展开成 `anyOf`：\n"
        + "\n".join(hits)
        + "\n请确认这是你要的 schema（逐分支展开，不再是改造前的裸 {\"type\": \"object\"}）。"
    )
