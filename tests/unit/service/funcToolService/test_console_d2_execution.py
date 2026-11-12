"""D2 隐藏模式下的 **console 真实执行路径** 判据（#166-A 修复后的口径）。

本文件与 `test_console_meta.py` 的分工：
- `test_console_meta.py`：**元数据**级（schema 形态、暴露面、命令表静态约束）。
- 本文件：**执行**级——真的构建注册表、真的抛 `ForwardToolCall`、真的看第 2 跳能不能到目标。

## #166-A 修复前的问题（历史）

`console` 抛 `ForwardToolCall` 后由 `execute_tool_call` 第 2 跳执行。修复前三个 Driver
只注册 `get_tools()` 返回的工具，而 `get_tools()` 会按 D2 开关过滤掉 16 个已收敛工具，
于是隐藏模式下第 2 跳 `registered is None`，23 条命令全部返回「未知工具」。

## 修复后的口径（本文件守护的性质）

`ToolVisibility` 把**注册面**与**暴露面**分开：

- Driver 用 `get_all_tools()` 全量注册（`visibility != NONE`），第 2 跳总能找到目标；
- `get_tools()` / `export_openai_tools()` / Claude SDK 的 MCP 工具列表只保留
  `tool_visible`；
- `execute_tool_call` 第 1 跳要求 `tool_visible`、第 2 跳要求 `console_visible`；
- D2 开关把已收敛工具的可见性在 `BOTH`（默认）与 `CONSOLE_ONLY`（隐藏）之间动态解析。

所以隐藏模式下：**模型直接调 `save_dept` 被拒；console 转发到 `save_dept` 可达**。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from constants import ToolVisibility
from exception import ForwardToolCall
from service.agentService import toolRegistry
from service.funcToolService import consoleCommands as cc
from service.funcToolService import core as func_tool_core
from util import llmApiUtil


#: 去重后的**全部**转发目标（含 #187 新接进来的 task 三工具）。
FORWARD_TARGETS: tuple[str, ...] = tuple(
    sorted({s.legacy_tool for s in cc.COMMAND_SPECS if s.legacy_tool})
)
#: 全部带转发目标的命令 key（参数化用，与 COMMAND_SPECS 同源，不手抄）。
FORWARD_COMMANDS: tuple[str, ...] = tuple(s.key for s in cc.COMMAND_SPECS if s.legacy_tool)
#: 落在 D2 隐藏清单内的转发目标 —— 隐藏模式下变为 `CONSOLE_ONLY`。
CONVERGED_FORWARD_TARGETS: tuple[str, ...] = tuple(
    t for t in FORWARD_TARGETS if t in cc.LEGACY_CONVERGED_TOOLS
)
#: **不**在隐藏清单内的转发目标（#187：task 三工具）—— 隐藏模式下仍是 `BOTH`。
NON_CONVERGED_FORWARD_TARGETS: tuple[str, ...] = tuple(
    t for t in FORWARD_TARGETS if t not in cc.LEGACY_CONVERGED_TOOLS
)


@pytest.fixture
def hidden_mode(monkeypatch: pytest.MonkeyPatch):
    """进入 D2 隐藏模式（并在用例结束后恢复）。"""
    monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, "0")
    assert func_tool_core.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"
    return monkeypatch


def _build_registry() -> toolRegistry.AgentToolRegistry:
    """按 Driver 的方式构建注册表：`get_all_tools()` 全量注册（#166-A）。

    顺序要紧：先 `load_func_tools()` 填 `_func_tools`，再 `get_all_tools()`。
    """
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools():
        registry.register(tool, func_tool_core.run_tool_call)
    return registry


def _register_console_stub(
    registry: toolRegistry.AgentToolRegistry, target: str, args: dict | None = None
) -> None:
    """注入只抛 `ForwardToolCall` 的 console 替身，把第 2 跳隔离出来测。"""

    async def _stub_console(args_str: str, ctx) -> dict:
        raise ForwardToolCall(target, args or {})

    console_tool = llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name="console",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(
                type="object", properties={}, required=[]
            ),
        )
    )
    registry.register(console_tool, _stub_console)


async def _forward_via_registry(
    registry: toolRegistry.AgentToolRegistry, target: str, args: dict | None = None
) -> toolRegistry.ToolExecutionResult:
    _register_console_stub(registry, target, args)
    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc_d2_hop", function={"name": "console", "arguments": "{}"}
    )
    return await registry.execute_tool_call(tool_call, context=ctx)


# ---------------------------------------------------------------------------
# 命令表结构：转发目标与隐藏清单的关系（不依赖 D2 开关）
# ---------------------------------------------------------------------------

def test_forward_targets_are_exactly_the_converged_set():
    """已收敛的转发目标集合必须精确等于 `LEGACY_CONVERGED_TOOLS`。

    ⚠️ #187 起「全部转发目标 == 收敛清单」不再成立：`task ls|get|update` 把
    `list_tasks` / `get_task_detail` / `update_task` 接成了转发目标，但按裁定②
    **不**扩 `LEGACY_CONVERGED_TOOLS`。于是「转发目标 ⊋ 收敛清单」。

    因此这里守两条：
    ① 收敛清单里的每个工具**都必须**有命令在转发它（清单里不得有僵尸项）；
    ② 差额只能是 `NON_CONVERGED_FORWARD_TARGETS` 这一组，且该组必须显式非空。
    """
    assert set(CONVERGED_FORWARD_TARGETS) == set(cc.LEGACY_CONVERGED_TOOLS), (
        f"有收敛清单成员没有任何命令转发它：{set(cc.LEGACY_CONVERGED_TOOLS) - set(CONVERGED_FORWARD_TARGETS)}"
    )
    assert set(FORWARD_TARGETS) == (
        set(cc.LEGACY_CONVERGED_TOOLS) | set(NON_CONVERGED_FORWARD_TARGETS)
    )
    assert set(NON_CONVERGED_FORWARD_TARGETS) == {
        "list_tasks", "get_task_detail", "update_task",
    }, f"未收敛的转发目标集合变了：{set(NON_CONVERGED_FORWARD_TARGETS)}"


def test_forward_command_count():
    """报总数：全部命令、带转发目标的命令、去重目标数。"""
    assert len(cc.COMMAND_SPECS) == 26, f"命令表已变化（现 {len(cc.COMMAND_SPECS)} 条），请复核本条"
    assert len(FORWARD_COMMANDS) == 26
    assert len(FORWARD_TARGETS) == 19
    assert len(cc.LEGACY_CONVERGED_TOOLS) == 16, "收敛清单必须仍是 16 项（#187 裁定②：本单不动）"
    assert cc.HELP_SPEC.legacy_tool == ""


# ---------------------------------------------------------------------------
# 注册面：两种模式下所有转发目标都必须注册（#166-A 的核心判据）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", FORWARD_TARGETS)
def test_hidden_mode_every_forward_target_is_registered(target, hidden_mode):
    """隐藏模式下**所有**转发目标仍在注册表里——注册面与暴露面已分离。"""
    registry = _build_registry()
    assert registry.get_registered_tool(target) is not None, (
        f"隐藏模式下 `{target}` 未注册 —— 注册面又被暴露面过滤了（#166-A 回归）"
    )


@pytest.mark.parametrize("target", FORWARD_TARGETS)
def test_default_mode_every_forward_target_is_registered(target, monkeypatch):
    """默认模式下同样全部注册。"""
    monkeypatch.delenv(cc.ENV_KEEP_LEGACY_TOOLS, raising=False)
    assert func_tool_core.keep_legacy_admin_tools() is True
    registry = _build_registry()
    assert registry.get_registered_tool(target) is not None, f"默认模式下 `{target}` 未注册"


# ---------------------------------------------------------------------------
# 执行面：隐藏模式下 console 转发**可达**（修复 #166-A 后的正向判据）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", FORWARD_TARGETS)
async def test_hidden_mode_forwarding_reaches_target_handler(target, hidden_mode):
    """隐藏模式下 console 的第 2 跳能真正执行到目标 handler。

    用计数 handler 替换目标工具，把「可达」与「目标工具自身的参数/DB 前置条件」
    解耦：否则缺参/无 DB 的失败会被误读成「仍然不可达」。
    """
    registry = _build_registry()
    called: list[dict] = []

    async def _counting_handler(args_str: str, ctx) -> dict:
        called.append({"args": args_str, "tool": ctx.tool_name})
        return {"success": True, "message": "reached"}

    registered = registry.get_registered_tool(target)
    assert registered is not None
    registered.handler = _counting_handler

    result = await _forward_via_registry(registry, target, {"probe": 1})

    assert result.success is True, result.result
    assert result.result.get("message") == "reached", result.result
    assert called and called[0]["tool"] == target, called


async def test_hidden_mode_unknown_target_is_still_reported(hidden_mode):
    """注册面放开不等于放行任意名字：真正不存在的工具仍报「未知工具」。"""
    registry = _build_registry()
    result = await _forward_via_registry(registry, "no_such_tool")
    assert result.success is False
    assert result.result.get("message") == "未知工具: no_such_tool", result.result


# ---------------------------------------------------------------------------
# 可见性与执行入口：hop 0 要求 tool_visible，hop 1 要求 console_visible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", CONVERGED_FORWARD_TARGETS)
def test_hidden_mode_converged_targets_are_console_only(target, hidden_mode):
    """隐藏模式下已收敛工具的动态可见性是 `CONSOLE_ONLY`（不只是"没暴露"）。"""
    registry = _build_registry()
    registered = registry.get_registered_tool(target)
    assert registered is not None
    assert registered.effective_visibility == ToolVisibility.CONSOLE_ONLY, target


@pytest.mark.parametrize("target", NON_CONVERGED_FORWARD_TARGETS)
def test_hidden_mode_task_targets_stay_both(target, hidden_mode):
    """task 三工具不在收敛清单内，隐藏模式下仍是 `BOTH`（裁定②的现状）。"""
    registry = _build_registry()
    registered = registry.get_registered_tool(target)
    assert registered is not None
    assert registered.effective_visibility == ToolVisibility.BOTH, target


async def test_hidden_mode_direct_call_to_console_only_tool_is_rejected(hidden_mode):
    """隐藏模式下模型直接调用 `save_dept`（hop 0）→ 可见性拒绝，提示走 console。"""
    registry = _build_registry()
    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc_direct", function={"name": "save_dept", "arguments": "{}"}
    )
    result = await registry.execute_tool_call(tool_call, context=ctx)
    assert result.success is False
    assert result.result.get("message") == "工具不能直接调用，请通过 console 命令使用: save_dept", (
        result.result
    )


async def test_tool_only_target_cannot_be_forwarded_from_console():
    """第 2 跳要求 `console_visible`：TOOL_ONLY 工具不能被 console 转发。

    用 `load_skill` 做探针：它不在任何 console 命令的转发目标里，可见性是 TOOL_ONLY。
    这条防止「注册面全量」被误读成「console 可以转发任意工具」。
    """
    registry = _build_registry()
    registered = registry.get_registered_tool("load_skill")
    assert registered is not None
    assert registered.effective_visibility == ToolVisibility.TOOL_ONLY

    result = await _forward_via_registry(registry, "load_skill")
    assert result.success is False
    assert result.result.get("message") == "工具不支持通过 console 调用: load_skill", (
        result.result
    )


def test_default_mode_converged_targets_are_both(monkeypatch):
    """默认模式下已收敛工具是 `BOTH`：独立 Tool 与 console 两个入口都可用。"""
    monkeypatch.delenv(cc.ENV_KEEP_LEGACY_TOOLS, raising=False)
    registry = _build_registry()
    for target in CONVERGED_FORWARD_TARGETS:
        registered = registry.get_registered_tool(target)
        assert registered is not None
        assert registered.effective_visibility == ToolVisibility.BOTH, target


# ---------------------------------------------------------------------------
# 暴露面：隐藏模式下已收敛工具不出现在模型工具列表（OpenAI 与 Claude 两个出口）
# ---------------------------------------------------------------------------

def test_hidden_mode_export_excludes_converged_tools(hidden_mode):
    """`export_openai_tools()` 按 `tool_visible` 过滤（native / tsp 的出口）。"""
    registry = _build_registry()
    exported = {tool.function.name for tool in registry.export_openai_tools()}
    assert not (set(cc.LEGACY_CONVERGED_TOOLS) & exported), (
        f"隐藏模式下仍暴露了已收敛工具：{set(cc.LEGACY_CONVERGED_TOOLS) & exported}"
    )
    assert "console" in exported
    for target in NON_CONVERGED_FORWARD_TARGETS:
        assert target in exported, target


def test_hidden_mode_claude_sdk_tool_list_excludes_converged_tools(hidden_mode):
    """`list_exposed_tool_names()` 与 OpenAI 出口同口径（Claude SDK MCP 用）。"""
    registry = _build_registry()
    exposed = set(registry.list_exposed_tool_names())
    enabled = set(registry.list_enabled_tool_names())
    assert not (set(cc.LEGACY_CONVERGED_TOOLS) & exposed)
    assert set(cc.LEGACY_CONVERGED_TOOLS) <= enabled, (
        "权限面（enabled）不受可见性影响：隐藏只改暴露面"
    )
