"""P0 独立基线可见性校验：49 个工具 × D2 开/关 = 98 个断言（设计 §7 P0-②）。

**为什么单独一个文件**：这是 P0 唯一的「不许按实现反推」判据。别处的可见性用例
（`test_console_d2_execution.py`）为了免抄写，是**从 `toolSpec.declared_specs()` 派生**
工具名单的——派生出来的表只能证明「实现自洽」，证明不了「和改前那 45 个工具的暴露面
一致」。所以这里手写一张字面期望表：工具名、基准可见性、D2 两侧的期望值，全部写死。

期望表的来源（写表时逐项对照，不是跑代码抄出来的）：

1. **改前基线** `9dd7f150`：`load_func_tools()` 注册表 39 项 + `CONTAINER_TOOL_NAMES` 6 项
   = 45 项；基准可见性由 `_CONSOLE_FORWARD_TARGETS`（console 转发靶子 → `BOTH`）、
   `_EXPLICIT_VISIBILITY`（`openapi_call` / `graphql_call` → `CONSOLE_ONLY`）、
   `_CONTAINER_TOOL_VISIBILITY`（`update_container_note` → `CONSOLE_ONLY`）三者确定，
   其余 `TOOL_ONLY`。
2. **设计 §4.3 / §7-P0②** 只允许两处偏离基线：
   - 4 个 `save_*` 拆成 8 个 `create_*` / `update_*`（45 → 49），可见性随原工具不变；
   - D2 关闭时 `BOTH` **统一**降 `CONSOLE_ONLY`（废止 #187 裁定②），也就是 `task` 四工具
     与 MCP 注册表四工具不再豁免。
   除这两条，其余工具的两侧可见性必须与基线逐项全等。
3. **数量自检**：28 `BOTH` + 3 `CONSOLE_ONLY` + 18 `TOOL_ONLY` = 49。

若本文件与实现不一致，**先怀疑实现**（这正是不许派生的理由）；确认实现无误后再回来
改字面表，并在报告里写明改动原因。
"""

from __future__ import annotations

import pytest

from constants import ToolVisibility
from service.funcToolService import core as func_tool_core
from service.funcToolService import toolSpec

# ---------------------------------------------------------------------------
# 字面期望表：工具名 → 基准（静态声明）可见性
#
# 分组顺序按「业务域」排，与实现里的声明顺序无关；任何一项被改动都会直接打红，
# 不做「按实现排序后比对集合」的宽松处理。
# ---------------------------------------------------------------------------
_BOTH = ToolVisibility.BOTH
_CONSOLE_ONLY = ToolVisibility.CONSOLE_ONLY
_TOOL_ONLY = ToolVisibility.TOOL_ONLY

EXPECTED_DECLARED: dict[str, ToolVisibility] = {
    # --- 基础能力 ---------------------------------------------------------
    "get_time": _BOTH,          # console `time`（基线即 BOTH）
    "web_search": _TOOL_ONLY,
    "webfetch": _TOOL_ONLY,
    "openapi_call": _CONSOLE_ONLY,    # 只作 console `openapi` 的第 2 跳靶子
    "graphql_call": _CONSOLE_ONLY,    # 同上
    # --- 消息与回合 -------------------------------------------------------
    "send_chat_msg": _TOOL_ONLY,
    "finish_action": _TOOL_ONLY,
    "start_chat": _BOTH,
    "wake_up_agent": _BOTH,
    "reload_team": _BOTH,
    # --- 组织查询 ---------------------------------------------------------
    "get_dept_info": _BOTH,
    "get_agent_info": _BOTH,
    "get_room_info": _BOTH,
    # --- 部门（dept）------------------------------------------------------
    "create_dept": _BOTH,       # 原 save_dept
    "update_dept": _BOTH,       # 原 save_dept
    "delete_dept": _BOTH,
    # --- 成员（agent）-----------------------------------------------------
    "create_agent": _BOTH,      # 原 save_agent
    "update_agent": _BOTH,      # 原 save_agent
    # --- 房间（room）------------------------------------------------------
    "create_room": _BOTH,       # 原 save_room
    "update_room": _BOTH,       # 原 save_room
    "delete_room": _BOTH,
    # --- 角色模板（role）--------------------------------------------------
    "list_role_templates": _BOTH,
    "get_role_template": _BOTH,
    "create_role_template": _BOTH,   # 原 save_role_template
    "update_role_template": _BOTH,   # 原 save_role_template
    "delete_role_template": _BOTH,
    # --- 任务（task，D2 关闭时收敛）--------------------------------------
    "create_task": _BOTH,
    "update_task": _BOTH,
    "get_task_detail": _BOTH,
    "list_tasks": _BOTH,
    "list_task_events": _TOOL_ONLY,   # 基线即 TOOL_ONLY（console 无对应命令）
    "get_task_event": _TOOL_ONLY,     # 同上
    # --- MCP 注册表（D2 关闭时收敛）--------------------------------------
    "save_mcp_server": _BOTH,
    "delete_mcp_server": _BOTH,
    "list_mcp_servers": _BOTH,
    "set_mcp_server_enabled": _BOTH,
    # --- 技能与记忆 -------------------------------------------------------
    "load_skill": _TOOL_ONLY,
    "memory_list": _TOOL_ONLY,
    "memory_read": _TOOL_ONLY,
    "memory_search": _TOOL_ONLY,
    "memory_create": _TOOL_ONLY,
    "memory_update": _TOOL_ONLY,
    # --- 控制台 -----------------------------------------------------------
    "console": _TOOL_ONLY,      # 自身不进 console 转发面（否则自递归）
    # --- 容器组（6 项，TspAgentDriver 专属）------------------------------
    "create_container": _TOOL_ONLY,
    "enter_container": _TOOL_ONLY,
    "exit_to_host": _TOOL_ONLY,
    "list_containers": _TOOL_ONLY,
    "delete_container": _TOOL_ONLY,
    "update_container_note": _CONSOLE_ONLY,   # Operator 2026-11-13：只由 container note 转发
}

#: 容器组的**精确**成员（不是「至少包含」，多一个少一个都算回归）。
EXPECTED_CONTAINER_GROUP: tuple[str, ...] = (
    "create_container",
    "enter_container",
    "exit_to_host",
    "list_containers",
    "delete_container",
    "update_container_note",
)

#: D2 关闭时由 `BOTH` 收敛为 `CONSOLE_ONLY` 的 8 个工具 —— 即被废止的 #187 裁定②
#: 那批「按名字特例豁免」的对象。这不是反向推导，是设计 §4.3 明写的变化面。
D2_RECLASSIFIED: tuple[str, ...] = (
    "create_task",
    "update_task",
    "get_task_detail",
    "list_tasks",
    "save_mcp_server",
    "delete_mcp_server",
    "list_mcp_servers",
    "set_mcp_server_enabled",
)

#: 4 → 8 拆分表（设计 §2.1）：老工具名 → 新工具名，可见性必须随原工具。
SAVE_SPLIT: dict[str, tuple[str, ...]] = {
    "save_agent": ("create_agent", "update_agent"),
    "save_dept": ("create_dept", "update_dept"),
    "save_room": ("create_room", "update_room"),
    "save_role_template": ("create_role_template", "update_role_template"),
}


def _effective(name: str) -> ToolVisibility:
    """按**当前环境**解析某工具的生效可见性，并顺带核对基准声明与字面表一致。"""
    spec = toolSpec.get_declared_spec(name)
    assert spec is not None, f"工具未声明 `@tool`：{name}"
    assert spec.visibility == EXPECTED_DECLARED[name], (
        f"{name} 的基准可见性变了：实现={spec.visibility.name}，期望={EXPECTED_DECLARED[name].name}"
    )
    return toolSpec.resolve_effective_visibility(spec.visibility)


@pytest.fixture
def d2_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2 关闭（收敛模式）。"""
    monkeypatch.setenv(toolSpec.ENV_KEEP_LEGACY_TOOLS, "0")
    assert toolSpec.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"


@pytest.fixture
def d2_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2 开启（基线保留模式）。"""
    monkeypatch.setenv(toolSpec.ENV_KEEP_LEGACY_TOOLS, "1")
    assert toolSpec.keep_legacy_admin_tools() is True, "开关未生效，判据无意义"


# ---------------------------------------------------------------------------
# 清单本身
# ---------------------------------------------------------------------------

def test_inventory_is_exactly_49_tools():
    """工具清单恰好是表里这 49 个：不多（漏报收敛面）不少（表过时）。"""
    declared = {d.name for d in toolSpec.declared_tools()}

    assert len(declared) == 49, f"声明工具数 = {len(declared)}，设计 §7-P0 规定 49"
    assert declared == set(EXPECTED_DECLARED), (
        f"未登记的工具: {sorted(declared - set(EXPECTED_DECLARED))}；"
        f"表里已消失的工具: {sorted(set(EXPECTED_DECLARED) - declared)}"
    )


def test_expected_table_distribution():
    """28 BOTH + 3 CONSOLE_ONLY + 18 TOOL_ONLY = 49（字面表自洽）。"""
    counts = {v: 0 for v in ToolVisibility}
    for visibility in EXPECTED_DECLARED.values():
        counts[visibility] += 1

    assert counts[_BOTH] == 28
    assert counts[_CONSOLE_ONLY] == 3
    assert counts[_TOOL_ONLY] == 18
    assert sum(counts.values()) == 49


def test_container_group_is_exactly_the_6_known_tools():
    """容器组精确 6 项，且 `update_container_note` 必须 `CONSOLE_ONLY` + `group="container"`。"""
    names = func_tool_core.container_tool_names()
    # 钉成员集合而非顺序：顺序只影响 `get_all_tools()` 的枚举次序，没有契约；
    # 多一项少一项都会改变 `include_container_tools=True` 的暴露面，必须精确。
    assert len(names) == 6, f"容器组必须 6 项，现 {len(names)}: {names}"
    assert set(names) == set(EXPECTED_CONTAINER_GROUP), (
        f"多出: {sorted(set(names) - set(EXPECTED_CONTAINER_GROUP))}；"
        f"缺少: {sorted(set(EXPECTED_CONTAINER_GROUP) - set(names))}"
    )

    note_spec = toolSpec.get_declared_spec("update_container_note")
    assert note_spec is not None
    assert note_spec.visibility == _CONSOLE_ONLY, "改容器说明是人的整理动作，模型不许直调"
    assert note_spec.group == "container"
    # 容器组其余 5 项都是 TOOL_ONLY（随组整体暴露给 TspAgentDriver）
    for name in EXPECTED_CONTAINER_GROUP:
        if name != "update_container_note":
            assert EXPECTED_DECLARED[name] == _TOOL_ONLY


def test_save_style_tools_are_gone():
    """`save_*` 已删（设计 §2.1）：共 4 → 8，可见性随原工具。"""
    for old, new_names in SAVE_SPLIT.items():
        assert toolSpec.get_declared_spec(old) is None, f"{old} 应已删除"
        for new_name in new_names:
            assert toolSpec.get_declared_spec(new_name) is not None, f"{new_name} 未声明"
            assert EXPECTED_DECLARED[new_name] == _BOTH, f"{new_name} 应随原工具为 BOTH"


# ---------------------------------------------------------------------------
# 98 个断言：49 个工具 × D2 开 / 关
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(EXPECTED_DECLARED))
def test_effective_visibility_d2_on(name: str, d2_on: None):
    """D2 开启 = 基线模式：生效可见性 == 基准可见性（除了下面那条规则，什么都不变）。"""
    assert _effective(name) == EXPECTED_DECLARED[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_DECLARED))
def test_effective_visibility_d2_off(name: str, d2_off: None):
    """D2 关闭 = 收敛模式：所有 `BOTH` 统一降 `CONSOLE_ONLY`，其余原样。"""
    expected = (
        _CONSOLE_ONLY if EXPECTED_DECLARED[name] == _BOTH else EXPECTED_DECLARED[name]
    )
    assert _effective(name) == expected


# ---------------------------------------------------------------------------
# 变化面：只许这 8 个工具在 D2 关闭时从「模型可见」变成「仅 console 可见」
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", D2_RECLASSIFIED)
def test_d2_off_hides_these_8_from_model_but_keeps_console_reachable(
    name: str, d2_off: None
):
    """收敛的 8 个工具：`tool_visible=False` 且 `console_visible=True`（#166-A 口径）。"""
    effective = _effective(name)

    assert effective.tool_visible is False, f"{name} 在 D2 关闭时不应出现在模型面板"
    assert effective.console_visible is True, f"{name} 仍必须能被 console 第 2 跳转发"


@pytest.mark.parametrize("name", D2_RECLASSIFIED)
def test_d2_on_keeps_these_8_model_visible(name: str, d2_on: None):
    """D2 开启时这 8 个仍可被模型直调（回归到基线行为）。"""
    effective = _effective(name)

    assert effective.tool_visible is True
    assert effective.console_visible is True


def test_former_187_exemption_is_abolished(d2_off: None):
    """#187 裁定②的 8 个豁免对象现在**也在**收敛集合内（设计 §4.3 / 变化 d）。

    两侧差异的集合精确 = 28 个 `BOTH`（旧代码里只有 16 个会收敛，task 四 + mcp 四被
    按名字豁免）。新规则是「所有 BOTH 一律收敛」，所以这 8 个必然落在差异集合中，
    且不能有额外的 `TOOL_ONLY` / `CONSOLE_ONLY` 工具被顺手改写。
    """
    converged = {
        name
        for name, declared in EXPECTED_DECLARED.items()
        if declared == _BOTH
        and toolSpec.resolve_effective_visibility(declared) == _CONSOLE_ONLY
    }

    assert len(converged) == 28, f"收敛集合应等于 28 个 BOTH，实测 {len(converged)}"
    assert set(D2_RECLASSIFIED) <= converged, (
        f"这些旧豁免工具没有收敛: {sorted(set(D2_RECLASSIFIED) - converged)}"
    )
    # 反向：`TOOL_ONLY` / `CONSOLE_ONLY` 两组在收敛模式下必须原样（不被误改）
    for name, declared in EXPECTED_DECLARED.items():
        if declared != _BOTH:
            assert toolSpec.resolve_effective_visibility(declared) == declared, name


# ---------------------------------------------------------------------------
# Smoke：收敛模式下「模型看不见」但「console 调得到」
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d2_off_smoke_model_panel_and_console_surface(d2_off: None):
    """D2 关闭时的两个面：`get_tools()` 不含 49 个非 TOOL_ONLY 的工具，console 仍可达。

    模型暴露面 = 18 个 `TOOL_ONLY`（含容器组 6 项，只有 `include_container_tools=True`
    才出现在 `get_tools()` 里）；28 个 `BOTH` 全部隐去，但它们的 console 命令仍在
    `consoleRegistry` 的命令表里、且解析后 `console_visible=True`。
    """
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()

    visible = {
        tool.function.name for tool in func_tool_core.get_tools(include_container_tools=True)
    }
    expected_visible = {n for n, v in EXPECTED_DECLARED.items() if v == _TOOL_ONLY}

    assert visible == expected_visible, (
        f"模型面板多出: {sorted(visible - expected_visible)}；"
        f"缺少: {sorted(expected_visible - visible)}"
    )
    for name in D2_RECLASSIFIED:
        assert name not in visible, f"{name} 在收敛模式下不该出现在模型面板"
        # 注册面不受影响：第 2 跳靠它找到目标（#166-A）
        assert name in {t.function.name for t in func_tool_core.get_all_tools(include_container_tools=True)}

    # console 的两个子树仍在控制台可达（`task ...` / `mcp ...`）。用**真调用**证明，
    # 不看表：`task` 走命令表 → 第 2 跳；`mcp` 走自己的窄解析器（不经命令表）。
    from exception import ForwardToolCall
    from service.funcToolService.consoleTool import console

    with pytest.raises(ForwardToolCall) as excinfo:
        await console("task ls --limit 1")
    assert excinfo.value.tool_name == "list_tasks", (
        f"收敛模式下 console task ls 应转发到 list_tasks，实际 {excinfo.value.tool_name}"
    )

    mcp_help = await console("mcp")
    assert mcp_help["success"] is True, mcp_help
    assert "mcp list" in mcp_help["text"], "收敛模式下 mcp 子树仍必须给出自己的用法"
