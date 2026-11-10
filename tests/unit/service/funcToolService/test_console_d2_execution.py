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
23/23 不可达）。运行时口径（真 console handler + 真 DB、**前置实体全部建成**后逐条跑）：
**23/23 条命令全部失败（0 成功），且全部断在第 2 跳 `未知工具: <legacy_tool>`**，
含 `dept get` / `dept update` / `dept rm` / `agent get` / `room update` / `room rm` /
`role update` / `role rm` / `reload`。连跑 3 次结果一致。

⚠️ 但**实体不存在时断点会漂到上游**：`dept get` / `dept rm` / `room rm` / `role rm` 这类
带 target 的命令会先报「未找到部门/房间/角色」，那时还没抛 `ForwardToolCall`。那不是
D2 的断因，是**前置条件没满足**——自测时若看到一批「未找到 X」，先怀疑探针实体没建好
（#166 两轮探针在这里翻过车），不要据此改这层结论。

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


#: 去重后的**全部**转发目标（含 #187 新接进来的 task 三工具）。
FORWARD_TARGETS: tuple[str, ...] = tuple(
    sorted({s.legacy_tool for s in cc.COMMAND_SPECS if s.legacy_tool})
)
#: 全部带转发目标的命令 key（参数化用，与 COMMAND_SPECS 同源，不手抄）。
FORWARD_COMMANDS: tuple[str, ...] = tuple(s.key for s in cc.COMMAND_SPECS if s.legacy_tool)
#: 落在 D2 隐藏清单内的转发目标 —— 隐藏模式下**应当**全部不可达。
CONVERGED_FORWARD_TARGETS: tuple[str, ...] = tuple(
    t for t in FORWARD_TARGETS if t in cc.LEGACY_CONVERGED_TOOLS
)
#: **不**在隐藏清单内的转发目标（#187：task 三工具）—— 隐藏模式下仍然可达。
NON_CONVERGED_FORWARD_TARGETS: tuple[str, ...] = tuple(
    t for t in FORWARD_TARGETS if t not in cc.LEGACY_CONVERGED_TOOLS
)


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
    """**已收敛的**转发目标集合必须精确等于 `LEGACY_CONVERGED_TOOLS`。

    ⚠️ #187 起这条不再写成「全部转发目标 == 收敛清单」：`task ls|get|update` 把
    `list_tasks` / `get_task_detail` / `update_task` 接成了转发目标，但按裁定②
    **不**扩 `LEGACY_CONVERGED_TOOLS`（该清单是 D2 开关语义载体，扩它等于改所有
    Agent 的工具面，须 Operator 决策）。于是「转发目标 ⊋ 收敛清单」。

    因此这里守两条，比原来更精确：
    ① 收敛清单里的每个工具**都必须**有命令在转发它（清单里不得有僵尸项）；
    ② 差额只能是 `NON_CONVERGED_FORWARD_TARGETS` 这一组，且这组必须**显式非空**
       地与下面「隐藏模式下仍可达」的事实对应 —— 不许悄悄为别的工具开口子。
    """
    assert set(CONVERGED_FORWARD_TARGETS) == set(cc.LEGACY_CONVERGED_TOOLS), (
        f"有收敛清单成员没有任何命令转发它：{set(cc.LEGACY_CONVERGED_TOOLS) - set(CONVERGED_FORWARD_TARGETS)}"
    )
    assert set(FORWARD_TARGETS) == (
        set(cc.LEGACY_CONVERGED_TOOLS) | set(NON_CONVERGED_FORWARD_TARGETS)
    )
    # 未收敛的转发目标只能是那三个任务工具（若将来又多出别的，本行必须被改，
    # 迫使作者回来重新评估「隐藏模式下它们仍可达」这件事）。
    assert set(NON_CONVERGED_FORWARD_TARGETS) == {
        "list_tasks", "get_task_detail", "update_task",
    }, f"未收敛的转发目标集合变了：{set(NON_CONVERGED_FORWARD_TARGETS)}"


def test_forward_command_count():
    """报总数：全部命令、带转发目标的命令、去重目标数。

    数字写死是有意的：命令表增删时要被迫回来看一眼隐藏模式是否还成立。
    #187 起 26 / 26 / 19（原 23 / 23 / 16，新增 task ls|get|update 三条）。
    """
    assert len(cc.COMMAND_SPECS) == 26, f"命令表已变化（现 {len(cc.COMMAND_SPECS)} 条），请复核本条"
    assert len(FORWARD_COMMANDS) == 26
    assert len(FORWARD_TARGETS) == 19
    assert len(cc.LEGACY_CONVERGED_TOOLS) == 16, "收敛清单必须仍是 16 项（#187 裁定②：本单不动）"
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

        # 前置：**已收敛**的目标确实没注册（否则下面「未知工具」的归因就不成立）
        registry = _build_registry()
        unregistered = [
            n for n in CONVERGED_FORWARD_TARGETS if registry.get_registered_tool(n) is None
        ]
        assert set(unregistered) == set(CONVERGED_FORWARD_TARGETS), (
            f"隐藏模式下应全部未注册，实际仍注册了：{set(CONVERGED_FORWARD_TARGETS) - set(unregistered)}"
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
# C-2：逐个转发目标验证（覆盖范围见下，两组各自的期望不同）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", CONVERGED_FORWARD_TARGETS)
async def test_d2_hidden_mode_every_forward_target_is_unreachable(target: str):
    """隐藏模式下**已收敛的**每个转发目标都不可达，且失败文案**精确指向该目标**。

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
    """默认模式下 19 个转发目标**全部**在注册表里。

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


# ---------------------------------------------------------------------------
# 对现状的显式记录：#187 的 task 三工具在隐藏模式下**仍然可达**
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", NON_CONVERGED_FORWARD_TARGETS)
def test_d2_hidden_mode_task_targets_stay_reachable(target: str):
    """隐藏模式下 task 三工具**依然注册**——这是 `LEGACY_CONVERGED_TOOLS` 未扩的直接后果。

    **本组是对现状的断言，不是对理想行为的断言。** 裁定②明确本单不扩收敛清单
    （那是 D2 开关的语义载体，须 Operator 决策），所以「task 命令在隐藏模式下仍
    可达」是已知且被接受的现状，性质是「未完成的收敛」，不是「不自洽」。

    为什么要专门写一组来钉住「它可达」：如果只把上一组的参数化范围从 19 缩到 16，
    「task 三工具在隐藏模式下能不能用」就变成了**没人守的空档**——将来有人顺手把
    它们加进收敛清单，测试不会红，而工具面已经变了。本组存在的意义是把「现状」
    变成可指认的事实：谁要改，谁就来改这条。

    与 `consoleCommands.LEGACY_CONVERGED_TOOLS` 上方的注释、报告 §4「只报不改」
    是同一件事的三处留痕。
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, "0")
        assert func_tool_core.keep_legacy_admin_tools() is False, "开关未生效，判据无意义"
        registry = _build_registry()
        assert registry.get_registered_tool(target) is not None, (
            f"隐藏模式下 `{target}` 竟然未注册 —— 说明它已经被收进 LEGACY_CONVERGED_TOOLS，"
            "与裁定②「本单保持 16 项」不符，请复核本组与收敛清单"
        )
    finally:
        monkeypatch.undo()
