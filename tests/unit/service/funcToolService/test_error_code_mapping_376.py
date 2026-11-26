"""#376 B4 判据（第二步）：九类 70 处的最终错误码映射（按 message 文本钉，不按行号）。

为什么用 **AST 扫源码**而不是逐条调函数：
1. 66 处散在两个文件、分布在几十个函数里，逐条调需要各自的 DB/上下文替身，
   成本高且判据会淹没在 mock 里；
2. 这里要钉的是**映射本身**（这段文案 ⇒ 这个码）与**处数读数**（每批多少处），
   AST 正好逐处可复算，且**按 message 文本匹配，行号漂移不会假红**；
3. 判据强度并不因此变弱：任何一处退化成手写字典、或码被换掉，本文件立刻红。
   真入口的行为另由 `test_b4_scenarios_376.py` 逐场景实测。

口径（小马哥 2026-11-27 01:10 裁定）：
- 按「最终错误码」算，不按场景名算；
- 二义文案「不存在或不属于当前团队」**不泄漏存在性**，因此那两处保留既有的
  `invalid_stage_owner` / `invalid_reviewer`（比 `not_found` 更具体且同样不泄漏），
  **不降级**——这条与"给 not_found"的差异在报告里单列说明；
- 已有具体码的 4 处一律不动。
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[4] / "src"
TASK_SERVICE = SRC / "service" / "taskService.py"
TOOLS = SRC / "service" / "funcToolService" / "tools.py"

#: 九类 70 处的最终码分布（4 处已有具体码的记在 `KEPT_SPECIFIC` 里）
# 键是 `toolResult.<CONST>` 的常量名（判据按常量名收集，不必 import 常量，
# 这样常量值改了也不会让判据假绿）。
EXPECTED_CODES = {
    "NOT_FOUND": 29,  # 任务/事件 6 + 成员 10 + 部门 4 + 房间 6 + 角色模板 3
    "PERMISSION_DENIED": 9,  # 跨团队 5 + SYSTEM 只读 4
    "MISSING_CONTEXT": 24,
    "INVALID_TOOL_ARGS": 3,  # 角色模板 type 非法 + driver 非法 + 优先级非法
    "INVALID_STATE": 1,  # 房间成员不足 2 人
}

#: 九类里**原本就有具体码**的 4 处：更具体的码优于通用码，不许降级
KEPT_SPECIFIC = {
    "依赖任务不存在（可能已删除）": "task_depends_on_invalid_target",
    "不存在或不属于当前团队": ("invalid_stage_owner", "invalid_reviewer"),
    "只更新已存在的模板": "invalid_tool_args",
}


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _const_code(node: ast.AST) -> str | None:
    """`toolResult.NOT_FOUND` → "NOT_FOUND"；其它形态返回 None。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _failure_calls() -> list[tuple[str, str, str]]:
    """收集 `toolResult.failure(<msg>, toolResult.<CONST>)`：(文件, 文案源码, 码常量)。"""
    calls: list[tuple[str, str, str]] = []
    for path in (TASK_SERVICE, TOOLS):
        tree = _parse(path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "failure" or node.func.value.__dict__.get("id") != "toolResult":
                continue
            if len(node.args) < 2:
                continue
            code = _const_code(node.args[1])
            if code is None:
                continue
            msg = ast.get_source_segment(path.read_text(encoding="utf-8"), node.args[0]) or ""
            calls.append((path.name, msg, code))
    return calls


def _bare_failure_dicts() -> list[tuple[str, str]]:
    """仍手写的 `{"success": False, "message": ...}`（不含 error_code）——即 backlog 读数。"""
    bare: list[tuple[str, str]] = []
    for path in (TASK_SERVICE, TOOLS):
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            if "success" not in keys or "error_code" in keys:
                continue
            first = node.values[keys.index("success")]
            if not (isinstance(first, ast.Constant) and first.value is False):
                continue
            bare.append((path.name, ast.get_source_segment(text, node) or ""))
    return bare


# --------------------------------------------------------------------------- #
# 1. 处数读数（每批多少处）
# --------------------------------------------------------------------------- #


def test_converged_code_distribution() -> None:
    """九类已收敛的 66 处，按最终码分组后的处数必须与裁定一致。"""
    counter = Counter(code for _, _, code in _failure_calls())

    assert dict(counter) == EXPECTED_CODES, (
        "九类码分布与裁定不一致：\n"
        f"实际={dict(counter)}\n期望={EXPECTED_CODES}\n"
        "（若确实要调整归类，先改报告里的口径说明，再改这里的期望值）"
    )


def test_backlog_count_is_44() -> None:
    """九类之外仍有 44 处手写失败字典（110 无码 − 66 已收敛），本批**不动**。

    读数为什么放判据里：`156 处 backlog` 是「226 − 70」算出来的，
    而 44 是「无码 110 − 已收敛 66」——两个口径必须能对上，
    否则报告里的"只做了九类"就会被处数悄悄推翻（越做越多或越做越少都算违约）。
    """
    bare = _bare_failure_dicts()

    assert len(bare) == 44, f"backlog 处数变了：{len(bare)}（期望 44）"
    # 顺手钉住"没有越界"：backlog 里若混入九类文案，说明有处漏收敛
    nine_marks = ("未找到成员", "未找到部门", "未找到房间", "未找到角色模板", "当前没有可用的团队上下文")
    leaked = [seg for _, seg in bare if any(m in seg for m in nine_marks)]
    assert leaked == [], f"九类文案仍有手写失败字典（漏收敛）：{leaked[:3]}"


# --------------------------------------------------------------------------- #
# 2. 逐类映射（按 message 文本）
# --------------------------------------------------------------------------- #


def _codes_of(*msg_fragments: str) -> set[str]:
    """给定文案片段，返回命中处的码集合。"""
    return {code for _, msg, code in _failure_calls() if all(f in msg for f in msg_fragments)}


def test_not_found_covers_existing_object_lookups() -> None:
    """任务/事件/成员/部门/房间/角色模板"不存在"，最终码都是 `not_found`。"""
    assert _codes_of("任务不存在") == {"NOT_FOUND"}
    assert _codes_of("事件不存在") == {"NOT_FOUND"}
    assert _codes_of("未找到成员") == {"NOT_FOUND"}
    assert _codes_of("未找到部门") == {"NOT_FOUND"}
    assert _codes_of("未找到房间") == {"NOT_FOUND"}
    assert _codes_of("未找到组织") == {"NOT_FOUND"}
    assert _codes_of("未找到角色模板") == {"NOT_FOUND"}


def test_cross_team_is_permission_denied() -> None:
    """「不属于当前团队」= 身份不够 ⇒ `permission_denied`（**明确的两义文案除外**）。"""
    assert _codes_of("任务不属于当前团队") == {"PERMISSION_DENIED"}
    assert _codes_of("事件不属于当前团队") == {"PERMISSION_DENIED"}
    assert _codes_of("依赖任务不属于当前团队") == {"PERMISSION_DENIED"}


def test_missing_context_maps_to_missing_context() -> None:
    """各种"没有上下文"的文案统一到 `missing_context`。"""
    for fragment in (
        "当前没有可用的团队上下文",
        "当前没有可用的房间上下文",
        "当前没有激活的上下文",
        "当前没有激活的房间上下文",
        "无法获取团队上下文",
        "无法获取当前 Agent 上下文",
    ):
        assert _codes_of(fragment) == {"MISSING_CONTEXT"}, fragment


def test_system_template_is_permission_denied() -> None:
    """SYSTEM 内置模板的增改删一律 `permission_denied`（不是 not_found：它明明存在）。"""
    assert _codes_of("SYSTEM 角色模板") == {"PERMISSION_DENIED"}
    assert _codes_of("不允许将角色模板") == {"PERMISSION_DENIED"}


def test_enum_illegal_args_map_to_invalid_tool_args() -> None:
    """枚举/取值非法是**参数**问题，走 `invalid_tool_args`（不是 invalid_state）。"""
    assert _codes_of("角色模板 type 只允许") == {"INVALID_TOOL_ARGS"}
    assert _codes_of("成员 driver 只允许") == {"INVALID_TOOL_ARGS"}
    assert _codes_of("无效的优先级") == {"INVALID_TOOL_ARGS"}


def test_room_members_insufficient_is_invalid_state() -> None:
    """房间已存在但成员不足 ⇒ **状态**不允许，不是参数错也不是不存在。"""
    assert _codes_of("房间成员不足 2 人") == {"INVALID_STATE"}


# --------------------------------------------------------------------------- #
# 3. 四类特判：已有具体码不降级（含二义文案的存证）
# --------------------------------------------------------------------------- #


def test_existing_specific_codes_are_not_downgraded() -> None:
    """九类里原本带具体码的 4 处保持原码——降级成通用码是**信息量损失**。

    这 4 处不在本轮改写清单里（`failure(...)` 清单里不该有它们的文案）；
    有人"顺手收敛"成通用码时，会在这里暴露。
    """
    for fragment in KEPT_SPECIFIC:
        codes = {code for _, msg, code in _failure_calls() if fragment in msg}
        assert codes == set(), f"{fragment!r} 被改写成通用码，原具体码丢失：{codes}"


def test_ambiguous_message_keeps_specific_code_and_no_leak() -> None:
    """二义文案「不存在或不属于当前团队」：保留既有具体码，**不新增存在性信息**。

    为什么这条要单独钉：裁定要求"码给 not_found、不给 permission_denied"，
    实测这两处的**既有码更具体**（`invalid_stage_owner` / `invalid_reviewer`），
    既不泄漏存在性、又能区分"缺的是节点负责人还是验收人"，
    因此保留而不降级；本条断言两个具体码仍在、且没有被追加 `not_found`。
    """
    text = TASK_SERVICE.read_text(encoding="utf-8")
    tree = _parse(TASK_SERVICE)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if "error_code" not in keys:
            continue
        msg = node.values[keys.index("message")]
        msg_text = ast.get_source_segment(text, msg) or ""
        if "不存在或不属于当前团队" in msg_text:
            code = node.values[keys.index("error_code")]
            found.append(getattr(code, "value", "?"))

    assert sorted(found) == ["invalid_reviewer", "invalid_stage_owner"], (
        f"二义文案的码变了：{sorted(found)}（期望既有两个具体码原样保留）"
    )
