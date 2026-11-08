"""D2 隐藏模式下的 **console 真实执行路径** 判据（#166 B + C）。

本文件与 `test_console_meta.py` 的分工：
- `test_console_meta.py`：**元数据**级（schema 形态、暴露面、命令表静态约束），不碰执行。
- 本文件：**执行**级——真的构建注册表、真的抛 `ForwardToolCall`、真的看第 2 跳能不能到目标。

为什么必须单独一个文件（#166 的由来）：
旧判据 `test_d2_get_tools_respects_switch_but_execution_does_not` 用
`get_func_tool(name) is not None` 代表「console 能路由到底层函数」。但 `get_func_tool()`
**不是 console 的执行路径**（#160 起 console 抛 `ForwardToolCall`，由 `execute_tool_call`
第 2 跳查注册表执行）。于是那条断言恒真、基线也绿，却守着一个假事实。

## 当前已验证的事实（不是待修目标，是**现状**）

隐藏模式（`TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS=0`）下：
- `get_tools()` 过滤掉 16 个原工具 ⇒ `tspDriver._register_host_tools` 不注册它们
  ⇒ `execute_tool_call` 第 2 跳 `registered is None` ⇒ 返回「未知工具: xxx」
- ⇒ **23 条带 `legacy_tool` 的命令、去重 16 个转发目标，全部不可达**

这与 `keep_legacy_admin_tools()` 旧 docstring 的承诺「关掉原工具后 console 依然能干活」
**相反**。该承诺自 #160（`a1e9a502`）起失效，已在 #165 `2da214f0` 的 docstring 中改正。

⚠️ **这些用例断言的是「不可达」，不是「可达」。** 理由：D2 的用途已确认为
「测 token 收益」（`shared/夜间值守汇总-20261108.md:90`：切到隐藏才净省 86.9%，
「要不要切由你定」——Operator 尚未拍板切产物），且 `keep_legacy_admin_tools()`
全仓仅一个调用点（`get_tools()` 过滤），无业务逻辑依赖隐藏模式，故当前不做功能修复。

**若将来决定把隐藏模式切进产物，必须先修好转发链路，届时这些用例会变红——这是故意的，
它是「切产物前先修好转发」的拦路闸。**
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from service.agentService import toolRegistry
from exception import ForwardToolCall
from service.funcToolService import consoleCommands as cc
from service.funcToolService import core as func_tool_core
from util import llmApiUtil


#: 去重后的转发目标（与 `LEGACY_CONVERGED_TOOLS` 精确相等——隐藏模式下 100% 断的根据）。
FORWARD_TARGETS: tuple[str, ...] = tuple(
    sorted({s.legacy_tool for s in cc.COMMAND_SPECS if s.legacy_tool})
)
#: 全部带转发目标的命令 key（参数化用，与 COMMAND_SPECS 同源，不手抄）。
FORWARD_COMMANDS: tuple[str, ...] = tuple(s.key for s in cc.COMMAND_SPECS if s.legacy_tool)


def _build_registry() -> toolRegistry.AgentToolRegistry:
    """按 `tspDriver._register_host_tools` 的方式构建注册表（不启真 TSP client）。

    顺序要紧：先 `load_func_tools()` 填 `_func_tools`，再 `get_tools()`。
    漏了加载，`get_tools()` 返回空列表 ⇒ 两种模式都注册 0 个工具，
    会打出「默认模式也全断」的假结论（#166 自查踩过）。
    """
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_tools():
        registry.register(tool, func_tool_core.run_tool_call)
    return registry


async def _forward_via_registry(
    registry: toolRegistry.AgentToolRegistry, target: str
) -> toolRegistry.ToolExecutionResult:
    """只驱动「第 2 跳」：模拟 console 抛出 `ForwardToolCall(target)` 之后会发生什么。

    为什么不逐条真跑 `console <cmd>`：带 target / accepts_input 的命令在**参数解析
    阶段**就要查库（解析部门名、成员名），单测环境没有 DB 会先炸在
    `Cannot use uninitialized Proxy.`——那时根本还没走到转发，归因就错了。
    （#166 自查踩过：14 条用例这样「失败」，看着像断因变了，其实是量具没装好。）

    本文件要守的**不是**参数解析，而是「console 抛出的转发目标能不能被执行」。
    所以注入一个只抛 `ForwardToolCall` 的 console 替身，把第 2 跳隔离出来测。
    它对应生产路径里 console handler 的唯一出口 `_forward`：
    `raise ForwardToolCall(parsed.spec.legacy_tool, args)`。
    """
    async def _stub_console(args_str: str, ctx) -> dict:
        raise ForwardToolCall(target, {})

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

    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc_d2_hop", function={"name": "console", "arguments": "{}"}
    )
    return await registry.execute_tool_call(tool_call, context=ctx)


# ---------------------------------------------------------------------------
# C-1：转发目标与隐藏清单的关系（把「100% 断」变成结构性事实，不靠人记）
# ---------------------------------------------------------------------------

def test_forward_targets_are_exactly_the_converged_set():
    """去重的转发目标集合必须**精确等于** `LEGACY_CONVERGED_TOOLS`。

    这条是「隐藏模式下 100% 断」的根据：转发目标全部落在隐藏清单内 ⇒ 一个都到不了。
    若将来新增的转发目标**不在**隐藏清单里，隐藏模式下它会仍然可达，那时本文件的
    「不可达」断言会红，需要重新评估。
    """
    assert set(FORWARD_TARGETS) == set(cc.LEGACY_CONVERGED_TOOLS), (
        f"转发目标与隐藏清单不一致：仅目标有={set(FORWARD_TARGETS) - set(cc.LEGACY_CONVERGED_TOOLS)}，"
        f"仅清单有={set(cc.LEGACY_CONVERGED_TOOLS) - set(FORWARD_TARGETS)}"
    )


def test_forward_command_count():
    """报总数：全部命令、带转发目标的命令、去重目标数。

    数字写死是有意的：命令表增删时要被迫回来看一眼隐藏模式是否还成立。
    """
    assert len(cc.COMMAND_SPECS) == 23, f"命令表已变化（现 {len(cc.COMMAND_SPECS)} 条），请复核本条"
    assert len(FORWARD_COMMANDS) == 23
    assert len(FORWARD_TARGETS) == 16
    # help 是纯本地命令（legacy_tool 为空），单独定义、不在 COMMAND_SPECS 内。
    assert cc.HELP_SPEC.legacy_tool == ""


# ---------------------------------------------------------------------------
# B：真实执行路径——隐藏模式下转发**不可达**（如实记录现状）
# ---------------------------------------------------------------------------

async def test_d2_hidden_mode_console_forwarding_is_unavailable():
    """隐藏模式下，console 的转发第 2 跳查不到目标 ⇒ 「未知工具」。

    这是**对现状的断言**，不是对理想行为的断言。它替代旧判据
    `..._but_execution_does_not` —— 后者用 `get_func_tool()` 代理执行路径，恒真假绿。

    三色（#166 B 要求）：
    - 旧判据在**任何**代码状态下都绿（因为它测的是字典 key）⇒ 证明它没测执行路径；
    - 本判据在 `2da214f0`（未修）上**绿**，记录现状「确实不可达」；
    - 把转发链路修好后本判据**红** ⇒ 证明它真的咬住了执行路径。
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, "0")
        assert func_tool_core.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"

        # 前置：目标确实没注册（否则下面「未知工具」的归因就不成立）
        registry = _build_registry()
        unregistered = [n for n in FORWARD_TARGETS if registry.get_registered_tool(n) is None]
        assert set(unregistered) == set(FORWARD_TARGETS), (
            f"隐藏模式下应全部未注册，实际仍注册了：{set(FORWARD_TARGETS) - set(unregistered)}"
        )
        assert registry.get_registered_tool("console") is not None, "console 本身必须仍在注册表"

        # 主断言：真走一遍转发，确认是「未知工具」而不是别的失败
        result = await _forward_via_registry(registry, "get_dept_info")
        assert result.success is False
        assert "未知工具" in str(result.result.get("message")), (
            f"期望「未知工具」，实际：{result.result}"
        )
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# C-2：逐个转发目标验证（16 个全覆盖）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", FORWARD_TARGETS)
async def test_d2_hidden_mode_every_forward_target_is_unreachable(target: str):
    """隐藏模式下**逐个转发目标**验证第 2 跳不可达。

    参数化而非循环，是为了失败时立刻看出是**哪一个**目标漏了。

    归因严格：必须因「未知工具」失败——不是「无权限」、不是「参数错」。
    若换成别的原因，说明断的机制变了，需要重新评估。
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, "0")
        assert func_tool_core.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"
        registry = _build_registry()

        result = await _forward_via_registry(registry, target)
        msg = str(result.result.get("message", ""))
        assert result.success is False, f"隐藏模式下转发到 `{target}` 竟成功了：{result.result}"
        assert "未知工具" in msg, (
            f"转发到 `{target}` 的失败原因不是「未知工具」而是：{msg!r}\n"
            f"→ 断的原因变了，请重新评估隐藏模式的实际行为"
        )
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# 反向守门：默认模式下同一批目标**必须**可达
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", FORWARD_TARGETS)
def test_default_mode_every_forward_target_is_registered(target: str):
    """默认模式下 16 个转发目标**全部**在注册表里。

    没有这一组，「隐藏模式不可达」就无法归因到 D2 —— 也可能只是目标名字写错了。
    本组绿 + 上一组绿 ⇒ 差异确实来自开关。
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delenv(cc.ENV_KEEP_LEGACY_TOOLS, raising=False)
        assert func_tool_core.keep_legacy_admin_tools() is True
        registry = _build_registry()
        assert registry.get_registered_tool(target) is not None, f"默认模式下 `{target}` 未注册"
    finally:
        monkeypatch.undo()
