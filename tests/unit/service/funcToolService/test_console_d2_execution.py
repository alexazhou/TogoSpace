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

⚠️ 上句是**静态**口径（按命令表逐条比对其 `legacy_tool` 是否在隐藏清单内 ⇒ 目标层面
23/23 不可达），不要读成「运行时 23 条都断在转发层」。真 console handler + 真 DB 逐条
实跑：**23/23 条命令全部失败（0 成功）**，但只有**能走到转发层**的那批返回
`未知工具: <legacy_tool>`；带 target / accepts_input 的命令（`dept get` / `dept update` /
`dept rm` / `agent get` / `role update` / `role rm` …）在**参数解析 / 实体查找 / 字段校验
阶段**即失败，那时还没抛 `ForwardToolCall`——断因在上游、**与 D2 无关**（那批在默认模式
下同样失败）。报数按两层分开说，别用一个总数把上游失败算进 D2 账上。

这与 `keep_legacy_admin_tools()` 旧 docstring 的承诺「关掉原工具后 console 依然能干活」
**相反**。该承诺自 #160（`a1e9a502`）起失效，已在 #165 `2da214f0` 的 docstring 中改正。

⚠️ **这些用例断言的是「不可达」，不是「可达」。** 这不是把缺陷当成正确，而是
**如实记录现状 + 把它变成切换前的拦路闸**：

隐藏模式下 console 的全部转发命令都会失败（见
`test_d2_hidden_mode_console_forwarding_is_unavailable`）。**因此切换本开关前必须先修
#166-A（注册表注册面与执行面解耦），否则 console 会整体失效。**
A 未修是有意为之：当前无切换需求（`shared/夜间值守汇总-20261108.md:90` 记着
「切到隐藏才净省 86.9%…要不要切由你定」——是**尚未拍板的开放决策**，不是已否决），
且改动会触及 `execute_tool_call` 的权限判定单点。

**注意「要不要切由你定」≠「不准备切」。** 它随时可能被拍成「切」，
所以 A 不是「永远不用做」，而是**「切」这个决策的阻塞前置条件**：
谁想切，第一件事不是改环境变量，是先做 A。届时本文件的「不可达」用例会变红——
**那是故意的，它在喊「先修 A」。**
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

    ⚠️ **替身注入的是去向，不是来源。** 这里的 `target` 由用例闭包给定，不经过
    `parsed.spec.legacy_tool`，所以本文件验的是「给定 target 后第 2 跳如何」（去向），
    **验不了「target 从哪来」**（来源）。实测反证：把 `consoleTool.py:353` 硬编码成
    `raise ForwardToolCall("delete_dept", ...)`（来源彻底坏掉），本文件 35 条**全绿**，
    一条都没咬住。咬住这一跳的是 `test_console_tool.py`（同一变异 50 failed）。
    —— 别把「生产 `forward.tool_name` 传递已被验」记在本文件账上（#166 M9）。
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
    """隐藏模式下，console 的转发第 2 跳查不到目标 ⇒ 「未知工具: <具体目标>」。

    这是**对现状的断言**，不是对理想行为的断言。它替代旧判据
    `..._but_execution_does_not` —— 后者用 `get_func_tool()` 代理执行路径，恒真假绿。

    三色（#166 B 要求）：
    - 旧判据在**任何**代码状态下都绿（因为它测的是字典 key）⇒ 证明它没测执行路径；
    - 本判据在 `2da214f0`（未修）上**绿**，记录现状「确实不可达」；
    - 把转发链路修好后本判据**红** ⇒ 证明它真的咬住了执行路径。

    ⚠️ **必须断言到具体目标工具名**，不能只断言「含『未知工具』」：
    后者在 console 自身故障、转发逻辑写错、目标名拼错等情况下**同样会绿**，
    于是「坏了但看起来像预期现状」就被掩盖了。这里断言的是完整字符串
    `未知工具: delete_dept`，把「按预期不可用」和「坏了」区分开。
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

        # 主断言：真走一遍转发，且必须是「未知工具: delete_dept」这个**精确**文案
        result = await _forward_via_registry(registry, "delete_dept")
        message = str(result.result.get("message", ""))
        assert result.success is False
        assert message == "未知工具: delete_dept", (
            f"期望精确的「未知工具: delete_dept」，实际：{message!r}\n"
            f"→ 若报的是别的工具名或别的失败原因，说明断的机制变了，不能当预期现状"
        )
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# C-2：逐个转发目标验证（16 个全覆盖）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", FORWARD_TARGETS)
async def test_d2_hidden_mode_every_forward_target_is_unreachable(target: str):
    """隐藏模式下**逐个转发目标**验证第 2 跳不可达，且失败文案**精确指向该目标**。

    参数化而非循环，是为了失败时立刻看出是**哪一个**目标漏了。

    断言用**正向表述**：必须等于 `未知工具: <该目标>` —— 不是「含未知工具」，
    也不是「success 为 False」。后两者太弱：
    - 任何失败（console 自身坏了、转发逻辑写错）都能满足 success=False；
    - 目标名拼错时「含未知工具」仍绿，掩盖了真实原因。
    精确匹配才能区分「按预期不可用」与「坏了但看起来一样」。
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, "0")
        assert func_tool_core.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"
        registry = _build_registry()

        result = await _forward_via_registry(registry, target)
        message = str(result.result.get("message", ""))
        expected = f"未知工具: {target}"
        assert result.success is False, f"隐藏模式下转发到 `{target}` 竟成功了：{result.result}"
        assert message == expected, (
            f"转发到 `{target}` 期望精确文案 {expected!r}，实际 {message!r}\n"
            f"→ 断的原因或目标名变了，请重新评估隐藏模式的实际行为"
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
