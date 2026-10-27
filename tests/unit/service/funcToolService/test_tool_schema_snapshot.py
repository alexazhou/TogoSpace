"""#73 / #75：工具 schema 快照护栏（替代不可复现的哈希指纹）。

把 `tools.py` 里每个本地工具的 **description、参数说明、参数 JSON Schema
（type / enum / array…）与工具级 required**，与已提交的
`tests/fixtures/tool_schema_snapshot.json` 逐字段对比。任何变化都会让本测试变红，
且失败信息是**结构化 diff**（哪个工具的哪句话 / 哪个参数的 schema / required 变了，
改前→改后），**禁止只给哈希**。

如为有意变更：运行

    python scripts/tool_schema_snapshot.py --update

更新快照，**并在 commit message 里说明变更原因**。本测试**不自动更新快照**
（不允许 CI 静默改写）；`--update` 是唯一合法的快照更新入口。

顺带记两条写工具 docstring 的口径（#72 收口时定）：
- 私有参数（`_context` 等）不要写进 `Args:` 段 —— 不在签名合法参数名集合里，
  会被当成续行并入上一个参数的说明；
- 参数说明里别夹 `NOTE:` / `TODO:` 这类标签行 —— 想给模型看的内容写首段或参数说明本身。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "tool_schema_snapshot.py"


def _load_snapshot_module():
    """按文件路径加载 `scripts/tool_schema_snapshot.py`（复用同一份口径，避免各写一套）。"""
    spec = importlib.util.spec_from_file_location("tool_schema_snapshot", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"无法加载脚本：{SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_schema_matches_committed_snapshot():
    """工具 schema（description + 每个参数的说明）必须与快照文件完全一致。

    失败时输出的是逐工具的结构化 diff（改前→改后），而不是哈希值 —— 目的是让人
    一眼看出「模型的工具说明到底哪句被改了」，而不是只知道「变了」。
    """
    module = _load_snapshot_module()

    expected = module.load_snapshot()
    actual = module.build_snapshot()

    assert actual == expected, module.format_diff(expected, actual)


# ---------------------------------------------------------------------------
# C-2：参数**声明顺序**（快照看不见的那一维）
# ---------------------------------------------------------------------------
#
# 为什么不塞进快照文件：`scripts/tool_schema_snapshot.py:96` 用
# `json.dumps(..., sort_keys=True)` 序列化，`parameters` 这个 dict 的键会被排序 ⇒
# 快照里根本没有声明顺序。实测：交换 `save_room` 的 `initial_topic` / `max_rounds`
# （两个都非必填）后，快照对比测试仍然 `1 passed`，一行不红。
#
# 必填参数看起来「有保护」，但那是偶然的：交换两个必填参数会让 `required` 数组顺序变，
# 快照才顺带红；两个非必填参数互换则完全没有信号。
#
# 所以这里选**方案 ②**（fixture 零 diff）：**现场**用 `get_function_metadata()` 取
# `parameters.properties` 的真实键序（实测该 dict 保序 = `inspect.signature` 顺序），
# 与下面这份固定清单逐工具比对。不选方案 ①（快照里加 `parameter_order` 字段）的原因：
# 那要全量重排 fixture（26 个工具、128 个参数的重排噪声），而收益与方案 ② 完全等价
# —— 都能看见顺序变化；方案 ② 的 diff 只有本文件这几行。
#
# 维护口径：**新增/删除/重排参数都要同步改这张表**。这是有意的——参数顺序是模型看到的
# 接口形态的一部分（必填在前更易被正确填充），变化必须显式、可读、有人签字。
_DECLARED_PARAMETER_ORDER: dict[str, list[str]] = {
    "create_task": ["title", "assignee_id", "description", "reviewer_id", "priority", "parent_id", "depends_on", "room_id"],
    "delete_dept": ["name", "dept_id", "recursive"],
    "delete_role_template": ["role_name"],
    "delete_room": ["name", "room_id"],
    "finish_action": ["confirm_no_need_talk"],
    "get_agent_info": ["agent_name"],
    "get_dept_info": ["dept_id"],
    "get_role_template": ["role_name"],
    "get_room_info": ["room_name"],
    "get_task_detail": ["task_id", "event_limit"],
    "get_task_event": ["event_id"],
    "get_time": ["timezone"],
    "list_role_templates": ["keywords"],
    "list_task_events": ["task_id", "limit", "before_id"],
    "list_tasks": ["assignee", "reviewer", "scope", "status", "title_keyword", "open_only", "limit"],
    "load_skill": ["skill_name"],
    "reload_team": [],
    "save_agent": ["name", "role_template_name", "model", "driver", "allow_tools", "allow_skills", "i18n", "overwrite_existing", "agent_id"],
    "save_dept": ["name", "responsibility", "manager_name", "member_names", "parent_name", "i18n", "overwrite_existing", "dept_id"],
    "save_role_template": ["name", "type", "soul", "i18n", "overwrite_existing"],
    "save_room": ["name", "member_names", "initial_topic", "max_rounds", "overwrite_existing", "room_id"],
    "send_chat_msg": ["room_name", "msg"],
    "start_chat": ["agent_name"],
    "update_task": ["task_id", "new_status", "note"],
    "wake_up_agent": ["agent_name"],
    "web_search": ["query"],
}


def _live_parameter_order() -> dict[str, list[str]]:
    """现场取每个本地工具的 `parameters.properties` 键序（= 声明顺序，不排序）。"""
    module = _load_snapshot_module()
    module._ensure_src_on_path()

    from service.agentService.toolRegistry import CATEGORY_CONFIG
    from service.funcToolService import tools
    from service.funcToolService.funcToolType import get_function_metadata

    order: dict[str, list[str]] = {}
    for tool_name in sorted(CATEGORY_CONFIG):
        func = getattr(tools, tool_name, None)
        if func is None:  # 非 tools.py 内的本地工具（如 TSP）
            continue
        order[tool_name] = list(get_function_metadata(tool_name, func)["parameters"]["properties"])
    return order


def _format_order_diff(expected: dict[str, list[str]], actual: dict[str, list[str]]) -> str:
    """逐工具列出「参数声明顺序」的差异（改前 → 改后），供失败信息用。"""
    lines: list[str] = []
    for tool_name in sorted(set(expected) - set(actual)):
        lines.append(f"[{tool_name}] 基表里有、代码里没有（工具被删了？）")
    for tool_name in sorted(set(actual) - set(expected)):
        lines.append(
            f"[{tool_name}] 代码里新增了工具，基表未登记：实际参数顺序 {actual[tool_name]!r}"
        )
    for tool_name in sorted(set(expected) & set(actual)):
        old, new = expected[tool_name], actual[tool_name]
        if old == new:
            continue
        lines.append(f"[{tool_name}] 参数顺序变化：")
        lines.append(f"  - 旧: {old!r}")
        lines.append(f"  + 新: {new!r}")
        if sorted(old) == sorted(new):
            lines.append("  （集合相同、仅顺序不同 —— 快照看不到这一维，正是本用例要抓的）")
        else:
            lines.append("  （参数集合也变了：新增/删除参数请同步更新基表）")
    return "\n".join(lines)


def test_parameter_declaration_order_matches_baseline():
    """参数**声明顺序**必须与基表一致（快照因 `sort_keys=True` 看不见顺序）。

    这条专治「交换两个非必填参数」：`save_room` 的 `initial_topic` / `max_rounds`
    互换后，快照测试仍会绿，只有本用例会红。
    """
    actual = _live_parameter_order()
    diff = _format_order_diff(_DECLARED_PARAMETER_ORDER, actual)

    assert not diff, (
        "工具参数声明顺序与基表不一致（注意：`tests/fixtures/tool_schema_snapshot.json` "
        "因序列化用了 `sort_keys=True`，**看不到**顺序变化，所以滑到这里才被发现）。\n"
        "若为有意变更，请同步修改本文件顶部的 `_DECLARED_PARAMETER_ORDER`"
        "并在 commit message 里说明为什么调整顺序。\n\n" + diff
    )


def test_parameter_order_baseline_covers_every_snapshot_tool():
    """基表必须与快照里的工具集**完全一致** —— 防止新工具静默绕过顺序护栏。

    没有这条，「加了一个新工具但忘了登记基表」会让顺序护栏悄悄失效（新工具永远不被检查）。
    """
    module = _load_snapshot_module()
    snapshot_tools = set(module.build_snapshot())
    baseline_tools = set(_DECLARED_PARAMETER_ORDER)
    live_tools = set(_live_parameter_order())

    assert baseline_tools == live_tools, (
        f"参数顺序基表与代码里的工具集不一致："
        f"缺 {sorted(live_tools - baseline_tools)}；多 {sorted(baseline_tools - live_tools)}"
    )
    assert snapshot_tools == live_tools, (
        f"快照与代码里的工具集不一致："
        f"快照缺 {sorted(live_tools - snapshot_tools)}；快照多 {sorted(snapshot_tools - live_tools)}"
    )
