"""`container note`（#203）的 console 侧判据与「模型侧看不到」的实测证据。

## 这组用例防什么

需求的核心不是「多一个能改 note 的函数」，而是**「模型不能直调、console 能调」**。
只断言「`visibility == CONSOLE_ONLY`」不算证据 —— 那测的是「我写的常量等于我写的
常量」。这里用**同一份注册表**上的两条可观察路径来证：

1. 模型侧（hop 0）：`execute_tool_call` 直调 `update_container_note` ⇒ 被可见性拒绝，
   文案是「工具不能直接调用，请通过 console 命令使用」；
2. console 侧（hop 1）：`console("container note ...")` ⇒ 真的执行到目标的 handler。

两条路径的差异**只可能**来自可见性判定：注册表、handler、参数完全相同。

## 位置参数映射（#203 拍板 A）

`container note <id> <text>` 依赖 `_handle_direct` 的 `positional[i] → param_names[i]`。
这一处是**通用处理器**的改动，所以除了新命令的正向判据，还额外钉住存量命令的行为
（仓库里所有命令行现在 `max_args <= 1`，只走 i=0 那条路）。
"""

from __future__ import annotations

import json

import pytest

from exception import ForwardToolCall
from service.agentService import toolRegistry
from service.funcToolService import consoleCommands as cc
from service.funcToolService import core as funcCore
from service.funcToolService.consoleTool import console
from service.roomService import ToolCallContext
from util import llmApiUtil

NOTE_TOOL = "update_container_note"


def _ctx(agent_id: int = 1, tool_name: str = NOTE_TOOL) -> ToolCallContext:
    return ToolCallContext(agent_id=agent_id, team_id=1, tool_name=tool_name)


def _registry() -> toolRegistry.AgentToolRegistry:
    """与 `TspAgentDriver` 同形的注册表（唯一会执行容器工具的驱动）。"""
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in funcCore.get_all_tools(include_container_tools=True):
        registry.register(tool, funcCore.run_tool_call)
    return registry


def _tool_call(name: str, args: dict) -> llmApiUtil.OpenAIToolCall:
    return llmApiUtil.OpenAIToolCall(
        id="tc_container_note",
        function={"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    )


# ---------------------------------------------------------------------------
# 1. 模型暴露面 vs 注册面（两份清单必须真的不一样）
# ---------------------------------------------------------------------------


def test_model_facing_tool_list_excludes_it_while_registry_keeps_it():
    """模型暴露面（`get_tools`）不含它，注册面（`get_all_tools`）必须含它。

    两个方向都要断言，单看一边都能「假绿」：
    - 只断言「不在模型面」⇒ 工具根本没注册进来（漏加进 `CONTAINER_TOOL_NAMES`）也绿；
    - 只断言「在注册面」⇒ 可见性写错成 `TOOL_ONLY`（模型能直调）也绿。
    console 转发靠的是注册面，模型直调看的是暴露面，需求要的正是这两者**不同**。
    """
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()

    exposed = {tool.function.name for tool in funcCore.get_tools(include_container_tools=True)}
    registered = {
        tool.function.name for tool in funcCore.get_all_tools(include_container_tools=True)
    }

    assert NOTE_TOOL not in exposed, "出现在模型工具列表里 ⇒ 模型能直改容器说明"
    assert NOTE_TOOL in registered, "不在注册面 ⇒ console 第 2 跳只会报「未知工具」"


def test_visibility_is_console_only_in_both_switch_states(monkeypatch):
    """`CONSOLE_ONLY` 是**基准声明**，不能被 D2 开关翻面。

    D2 开关只解释 `LEGACY_CONVERGED_TOOLS` 里的工具。这里显式把开关打到两个方向，
    判据都是 `CONSOLE_ONLY`：若有人把这个工具误加进那份清单，或者改成靠
    `_declared_visibility` 推导（那条路对容器工具组根本不生效），这里立刻红。
    """
    from constants import ToolVisibility

    funcCore.load_func_tools()
    for value in ("1", "0"):
        monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, value)
        registry = _registry()
        registered = registry.get_registered_tool(NOTE_TOOL)
        assert registered is not None, f"KEEP_LEGACY_ADMIN_TOOLS={value} 时未注册"
        assert registered.effective_visibility == ToolVisibility.CONSOLE_ONLY, value

    # 反向对照：同一时刻其它容器工具仍是 TOOL_ONLY（证明本用例不是恒真）
    monkeypatch.delenv(cc.ENV_KEEP_LEGACY_TOOLS, raising=False)
    registry = _registry()
    others = registry.get_registered_tool("list_containers")
    assert others is not None
    assert others.effective_visibility == ToolVisibility.TOOL_ONLY


# ---------------------------------------------------------------------------
# 2. 两条可观察路径：模型直调被拒 / console 转发成功
# ---------------------------------------------------------------------------


async def test_direct_call_from_model_side_is_rejected():
    """hop 0：模型直调 ⇒ 明确拒绝，并指向 console 命令。"""
    registry = _registry()
    result = await registry.execute_tool_call(
        _tool_call(NOTE_TOOL, {"container_id": "c0ffee123456", "note": "x"}),
        context=_ctx(),
    )

    assert result.success is False
    assert result.result.get("message") == (
        f"工具不能直接调用，请通过 console 命令使用: {NOTE_TOOL}"
    ), result.result


async def test_console_forwarding_reaches_the_target_handler():
    """hop 1：`console("container note ...")` 能真正执行到目标 handler。

    用「计数 handler」替换目标工具，把「可达」与「目标工具自身的 DB 前置条件」解耦：
    否则缺 DB 导致的失败会被误读成「console 仍不可达」。
    """
    registry = _registry()
    called: list[dict] = []

    async def _counting_handler(args_str: str, ctx) -> dict:
        called.append({"args": args_str, "tool": ctx.tool_name})
        return {"success": True, "message": "reached"}

    registered = registry.get_registered_tool(NOTE_TOOL)
    assert registered is not None
    registered.handler = _counting_handler

    # console 自己被转发出去时抛 ForwardToolCall —— 走的是与生产相同的入口
    result = await registry.execute_tool_call(
        _tool_call("console", {"command": 'container note c0ffee123456 "跑前端构建"'}),
        context=_ctx(tool_name="console"),
    )

    assert result.success is True, result.result
    assert result.result.get("message") == "reached", result.result
    assert called and called[0]["tool"] == NOTE_TOOL, called


# ---------------------------------------------------------------------------
# 3. 位置参数映射：新命令正确，存量命令不变（#203 拍板 A 的硬约束）
# ---------------------------------------------------------------------------


class _CaptureForward:
    """捕获 `_handle_direct` 抛出的转发参数（不执行目标工具）。"""

    def __init__(self) -> None:
        self.tool_name: str | None = None
        self.args: dict = {}

    def __call__(self, *_a, **_kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


async def _forward_args(command: str) -> tuple[str, dict]:
    """跑一遍 console，返回 (转发目标, 转发参数)；转发信号是控制流，单独接住。

    用 `async def` + `await`（不用 `asyncio.run`/`get_event_loop`）：`asyncio_mode=auto`
    下每个用例本来就在自己的 loop 里，自己再造/取 loop 在全量跑时会拿到已关闭的 loop
    （单跑绿、全量红，实测踩过）。

    ⚠️ 取的是 `forward.tool_args` 而不是 `forward.args`：后者是 `BaseException.args`
    这个内置描述符，拿到的是**键的元组**（`exception.py` 的注释里记着这个坑）。
    """
    with pytest.raises(ForwardToolCall) as excinfo:
        await console(command, _context=_ctx(tool_name="console"))
    forward = excinfo.value
    return forward.tool_name, dict(forward.tool_args)


async def test_container_note_maps_two_positionals_by_declaration_order():
    """`container note <id> <text>` ⇒ `{container_id: <id>, note: <text>}`。

    这里钉的是「位置参数按 schema 声明顺序映射」这条新语义本身：参数名取自 schema
    （`container_id` / `note`），不是手抄。
    """
    target, args = await _forward_args('container note c0ffee123456 "跑前端构建"')

    assert target == NOTE_TOOL
    assert args == {"container_id": "c0ffee123456", "note": "跑前端构建"}, args


async def test_container_note_explicit_empty_string_is_the_only_clear_form():
    """`""` 是**唯一**的清空写法：它必须真的作为空串转发下去。

    `note` 是位置参数，`shlex` 会保留显式空串；转发到工具层后由
    `normalize_container_note("")` → `None`（清空）。
    """
    _, args = await _forward_args('container note c0ffee123456 ""')

    assert args["note"] == "", args


async def test_container_note_missing_text_is_rejected_by_arity():
    """少给文本必须被 arity 拒，**不许静默清空**（拍板 ③ 的硬要求）。

    这是「漏打一个参数就把说明擦掉」的唯一防线：工具层拿到 `note=None` 无法区分
    「没给」与「要清空」，所以必须在解析层用 min_args 拦住。
    """
    result = await console("container note c0ffee123456", _context=_ctx(tool_name="console"))

    assert result["success"] is False
    assert result["error_code"] == "invalid_argument_count", result
    assert result["error_code"] != "unknown_command"  # 是 arity 拦的，不是命令没认出来


def test_container_note_is_the_only_new_container_command():
    """只开 `note` 一条容器命令，其它容器命令**不进** console。

    守住任务书的不做边界：`create/enter/exit/list/rm` 都没进命令表。这条不是形式主义
    —— 它们一旦进 console，`enter_container` 这种「切换执行环境」的动作就能被人在会话
    之外触发，绕开 Driver 的执行环境管理。
    """
    container_actions = sorted(
        spec.action for spec in cc.COMMAND_SPECS if spec.resource == "container"
    )
    assert container_actions == ["note"], container_actions


#: 存量 DIRECT 命令的「位置参数 → 工具参数」映射（改动前就是这样，必须逐字不变）。
_EXISTING_DIRECT_CASES = [
    ("agent wake bob", "wake_up_agent", {"agent_name": "bob"}),
    ("agent dm charlie", "start_chat", {"agent_name": "charlie"}),
    ("reload", "reload_team", {}),
]


@pytest.mark.parametrize("command,target,expected", _EXISTING_DIRECT_CASES)
async def test_existing_direct_commands_keep_their_mapping(command, target, expected):
    """存量 DIRECT 命令的转发参数逐字不变（`max_args <= 1`，只走 i=0）。

    这是「改通用处理器」的代价必须用判据兜住的地方：它们全部只给一个位置参数，
    新旧实现都映射到 `param_names[0]`，所以断言可以写成**改动前就该成立的值**。
    """
    actual_target, args = await _forward_args(command)

    assert actual_target == target
    assert args == expected, args


def test_no_console_command_exceeds_one_positional_except_container_note():
    """`max_args > 1` 的命令有且只有 `container note`（存量行为不变的结构证据）。

    这条同时是给 #202 的护栏：二部要给 `mcp call <target> <tool> [json]` 加位置参数时，
    这里会提醒他「多位置参数现在是受支持的通用能力，但每加一条都该在命令表里显式声明」。
    """
    multi = sorted(
        spec.key for spec in cc.COMMAND_SPECS if spec.max_args > 1 and not spec.accepts_input
    )
    assert multi == ["container note"], multi


# ---------------------------------------------------------------------------
# 4. help 文案：用法行与摘要行（补的判据 —— 变异验证发现的缺口）
# ---------------------------------------------------------------------------


def test_usage_line_lists_both_positionals_with_schema_names():
    """`help container note` 的用法行必须**逐个**列出两个位置参数。

    缺口来源：变异验证时把「多位置参数逐个占位」退回成「只写一个 `<target>`」，
    全套 633 条用例仍全绿 —— 也就是原先没有一条判据在管这件事。而它的后果是
    用户照着 help 敲 `container note <target>` 必然被 arity 拒（少一段），
    help 反而把用户带错，属于必须钉住的行为。

    占位名取 schema 声明顺序（`container_id` / `note`），不手抄。
    """
    from service.funcToolService.consoleTool import _usage_line

    spec = next(s for s in cc.COMMAND_SPECS if s.key == "container note")
    usage = _usage_line(spec)

    assert "<container_id>" in usage, usage
    assert "<note>" in usage, usage
    # 只有一个占位的旧形态（`<target>` / `<container_id>` 单独出现）必须已消失
    assert usage.count("<") == 2, usage


def test_help_summary_line_is_tokenizable_for_container():
    """`help` 摘要里 `container` 与 `note` 必须是**两个** token。

    缺口来源同上：对齐宽度写死 `:<8` 时 `container`（9 字符）会和操作名粘成
    `containernote`。原有关键断言是 `"container" in HELP_SUMMARY`，粘连后仍为真
    （子串命中），所以咬不住。这里改成按 token 切词比对。
    """
    from service.funcToolService.consoleTool import HELP_SUMMARY

    container_lines = [
        line for line in HELP_SUMMARY.splitlines() if line.split()[:1] == ["container"]
    ]
    assert len(container_lines) == 1, HELP_SUMMARY
    assert container_lines[0].split() == ["container", "note"], container_lines[0]
