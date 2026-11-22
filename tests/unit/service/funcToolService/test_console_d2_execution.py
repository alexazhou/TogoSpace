"""D2 开关、注册面 / 暴露面 / 执行面的口径（P0 重写）。

旧版围绕 `consoleCommands.LEGACY_CONVERGED_TOOLS`（16 项手写清单）与 `COMMAND_SPECS`
写结构断言。P0 把可见性收归 `ToolSpec`、废止 #187 裁定②（task / mcp 工具不再豁免收敛），
旧判据覆盖的模型整体消失，因此按新模型重写：

- **收敛规则**：D2 关闭时**所有** `BOTH` 工具统一降为 `CONSOLE_ONLY`（设计 §4.3 / §7-5）。
- **转发目标**：命令表的转发目标由 `@tool(console=...)` 派生；`mcp` / `openapi` /
  `graphql` 子树仍由 console 硬分派（P3 才 extension 化），其靶子工具单独列出。
- 注册面（`get_all_tools`）与暴露面（`get_tools` / `export_openai_tools`）分离不变。

**独立期望表**（98 条可见性断言）不在这里，见 `test_tool_visibility_baseline.py`：
本文件的目的是执行面覆盖（第 2 跳可达、直调被拒、暴露面过滤），因此列表从注册表派生。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from constants import ToolVisibility
from exception import ForwardToolCall
from service.agentService import toolRegistry
from service.funcToolService import consoleRegistry
from service.funcToolService import core as func_tool_core
from service.funcToolService import toolSpec
from util import llmApiUtil

#: `mcp` / `openapi` / `graphql` 子树硬分派会转发的靶子工具（P0 期间不由命令表派生）。
EXTENSION_FORWARD_TARGETS: tuple[str, ...] = (
    "save_mcp_server",
    "delete_mcp_server",
    "list_mcp_servers",
    "set_mcp_server_enabled",
    "openapi_call",
    "graphql_call",
)

#: 命令表里挂了 console 命令的工具（派生，不手抄）。
CONSOLE_BOUND_TOOLS: tuple[str, ...] = tuple(
    sorted({cmd.tool_name for cmd in consoleRegistry.get_table().specs if cmd.tool_name})
)

#: 全部 console 可达的转发目标 = 命令表转发 + 扩展子树靶子。
FORWARD_TARGETS: tuple[str, ...] = tuple(sorted(set(CONSOLE_BOUND_TOOLS) | set(EXTENSION_FORWARD_TARGETS)))

#: 全部带 console 命令的 key（参数化用）。
FORWARD_COMMANDS: tuple[str, ...] = tuple(cmd.key for cmd in consoleRegistry.get_table().specs if cmd.tool_name)

#: 基准可见性为 `CONSOLE_ONLY` 的工具（与 D2 开关无关）。
CONSOLE_ONLY_TOOLS: tuple[str, ...] = (
    "update_container_note",
    "openapi_call",
    "graphql_call",
)

#: 基准可见性为 `BOTH` 的工具（D2 关闭时全部收敛为 `CONSOLE_ONLY`）。
BOTH_TOOLS: tuple[str, ...] = tuple(
    sorted(name for name, spec in toolSpec.declared_specs() if spec.visibility == ToolVisibility.BOTH)
)


@pytest.fixture
def hidden_mode(monkeypatch: pytest.MonkeyPatch):
    """进入 D2 隐藏模式（并在用例结束后恢复）。"""
    monkeypatch.setenv(toolSpec.ENV_KEEP_LEGACY_TOOLS, "0")
    assert func_tool_core.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"
    return monkeypatch


def _build_registry() -> toolRegistry.AgentToolRegistry:
    """按 Driver 的方式构建注册表：`get_all_tools()` 全量注册（#166-A）。"""
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools(include_container_tools=True):
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
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[]),
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
# 命令表结构（不依赖 D2 开关）
# ---------------------------------------------------------------------------

def test_forward_command_count():
    """报总数：可见性收敛模型的转发面读数（P0 后 28 条命令 / 27 个转发目标）。"""
    table = consoleRegistry.get_table()
    assert len(table.specs) == 28, f"命令表已变化（现 {len(table.specs)} 条）"
    assert len(FORWARD_COMMANDS) == 28
    # 31 = 25 个命令表工具（dept 4 / agent 5 / room 4 / role 5 / task 4 / container 1 /
    #      time 1 / reload 1；`dept ls` 与 `dept get` 等共用同一个工具）+ 6 个扩展子树靶子
    assert len(FORWARD_TARGETS) == 31, f"转发目标数变了：{FORWARD_TARGETS}"
    assert table.help_spec.tool is None


def test_all_console_bound_tools_are_declared_both():
    """挂了 console 命令的工具，静态可见性必须是 `console_visible`（设计 §4.2）。"""
    for name in CONSOLE_BOUND_TOOLS:
        spec = toolSpec.get_declared_spec(name)
        assert spec is not None, name
        assert spec.visibility.console_visible, f"{name} 挂了命令却不可通过 console 使用"


def test_container_note_hard_assertion():
    """硬断言（设计 §6 P0 护栏②）：`update_container_note` 必须是 CONSOLE_ONLY + container 组。"""
    spec = toolSpec.get_declared_spec("update_container_note")
    assert spec is not None
    assert spec.visibility == ToolVisibility.CONSOLE_ONLY
    assert spec.group == "container"
    container = {name for name, s in toolSpec.declared_specs() if s.group == "container"}
    assert container == {
        "create_container",
        "enter_container",
        "exit_to_host",
        "list_containers",
        "delete_container",
        "update_container_note",
    }, f"容器组精确 6 项被改了：{sorted(container)}"


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
    monkeypatch.delenv(toolSpec.ENV_KEEP_LEGACY_TOOLS, raising=False)
    assert func_tool_core.keep_legacy_admin_tools() is True
    registry = _build_registry()
    assert registry.get_registered_tool(target) is not None, f"默认模式下 `{target}` 未注册"


# ---------------------------------------------------------------------------
# 执行面：隐藏模式下 console 转发**可达**（修复 #166-A 后的正向判据）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", FORWARD_TARGETS)
async def test_hidden_mode_forwarding_reaches_target_handler(target, hidden_mode):
    """隐藏模式下 console 的第 2 跳能真正执行到目标 handler。"""
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

@pytest.mark.parametrize("target", BOTH_TOOLS)
def test_hidden_mode_both_tools_are_console_only(target, hidden_mode):
    """隐藏模式下**所有** `BOTH` 工具统一收敛为 `CONSOLE_ONLY`（废止 #187 裁定②）。"""
    registry = _build_registry()
    registered = registry.get_registered_tool(target)
    assert registered is not None
    assert registered.effective_visibility == ToolVisibility.CONSOLE_ONLY, target


@pytest.mark.parametrize("target", BOTH_TOOLS)
def test_default_mode_both_tools_stay_both(target, monkeypatch):
    """默认模式下 `BOTH` 工具仍是 `BOTH`：独立 Tool 与 console 两个入口都可用。"""
    monkeypatch.delenv(toolSpec.ENV_KEEP_LEGACY_TOOLS, raising=False)
    registry = _build_registry()
    registered = registry.get_registered_tool(target)
    assert registered is not None
    assert registered.effective_visibility == ToolVisibility.BOTH, target


async def test_hidden_mode_direct_call_to_console_only_tool_is_rejected(hidden_mode):
    """隐藏模式下模型直接调用 `update_dept`（hop 0）→ 可见性拒绝，提示走 console。"""
    registry = _build_registry()
    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc_direct", function={"name": "update_dept", "arguments": "{}"}
    )
    result = await registry.execute_tool_call(tool_call, context=ctx)
    assert result.success is False
    assert result.result.get("message") == "工具不能直接调用，请通过 console 命令使用: update_dept", (
        result.result
    )


async def test_tool_only_target_cannot_be_forwarded_from_console():
    """第 2 跳要求 `console_visible`：TOOL_ONLY 工具不能被 console 转发。

    用 `load_skill` 做探针：它不在任何 console 命令的转发目标里，可见性是 TOOL_ONLY。
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


# ---------------------------------------------------------------------------
# 暴露面：隐藏模式下收敛工具不出现在模型工具列表（OpenAI 与 Claude 两个出口）
# ---------------------------------------------------------------------------

def test_hidden_mode_export_excludes_converged_tools(hidden_mode):
    """`export_openai_tools()` 按 `tool_visible` 过滤（native / tsp 的出口）。"""
    registry = _build_registry()
    exported = {tool.function.name for tool in registry.export_openai_tools()}
    assert not (set(BOTH_TOOLS) & exported), (
        f"隐藏模式下仍暴露了已收敛工具：{set(BOTH_TOOLS) & exported}"
    )
    assert "console" in exported


def test_hidden_mode_claude_sdk_tool_list_excludes_converged_tools(hidden_mode):
    """`list_exposed_tool_names()` 与 OpenAI 出口同口径（Claude SDK MCP 用）。"""
    registry = _build_registry()
    exposed = set(registry.list_exposed_tool_names())
    enabled = set(registry.list_enabled_tool_names())
    assert not (set(BOTH_TOOLS) & exposed)
    assert set(BOTH_TOOLS) <= enabled, "权限面（enabled）不受可见性影响：隐藏只改暴露面"
