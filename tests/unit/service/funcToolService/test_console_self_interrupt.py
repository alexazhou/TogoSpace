"""`console` 的动态自中断机制（#154 D6）。

为什么需要单独一组用例：`console` 是第一个「有时自中断、有时不自中断」的工具。
静态 `self_interrupt=True` 会让每条命令都留下中断标记，`False` 会让 `reload` 丢标记
导致重启后重放成第二次重载。两种错法的**症状都很隐蔽**（要真实重启才看得见），
所以在纯单元层面把判定逻辑钉死。
"""

from __future__ import annotations

import json

import pytest

from service.agentService.toolRegistry import AgentToolRegistry, RegisteredTool
from service.funcToolService.consoleTool import console_triggers_self_interrupt
from util.llmApiUtil import OpenAIToolCall


def _call(command: str) -> OpenAIToolCall:
    return OpenAIToolCall(
        id="c1",
        function={"name": "console", "arguments": json.dumps({"command": command}, ensure_ascii=False)},
    )


# ---------------------------------------------------------------------------
# RegisteredTool.triggers_self_interrupt：静态 / 动态 / 都没有
# ---------------------------------------------------------------------------

class TestTriggersSelfInterrupt:
    def _tool(self, *, static: bool = False, predicate=None) -> RegisteredTool:
        return RegisteredTool(tool=None, handler=None, self_interrupt=static, self_interrupt_predicate=predicate)

    def test_static_flag_wins(self):
        """静态标志为真时，无论参数如何都自中断（`reload_team` 的用法）。"""
        tool = self._tool(static=True)
        assert tool.triggers_self_interrupt(_call("whatever")) is True

    def test_no_flag_no_predicate_never_interrupts(self):
        """普通工具（两者都空）永不自中断。"""
        assert self._tool().triggers_self_interrupt(_call("dept ls")) is False

    def test_predicate_is_consulted(self):
        """只有判定器时，结果完全由它决定 —— 两个方向都要测。"""
        tool = self._tool(predicate=lambda call: call.function_args == "YES")
        assert tool.triggers_self_interrupt(_call("x")) is False
        tool2 = self._tool(predicate=lambda call: True)
        assert tool2.triggers_self_interrupt(_call("x")) is True

    def test_predicate_absent_after_clear_registry(self):
        """注册表 clear 后不得残留判定器（否则上一轮的判定会影响下一轮）。"""
        registry = AgentToolRegistry()
        from util import llmApiUtil

        tool = llmApiUtil.OpenAITool(
            function=llmApiUtil.OpenAIFunction(
                name="console",
                description="d",
                parameters=llmApiUtil.OpenAIFunctionParameter(
                    type="object", properties={}, required=[]
                ),
            )
        )
        registry.register(tool, handler=None, self_interrupt_predicate=lambda call: True)
        assert registry.get_registered_tool("console").triggers_self_interrupt(_call("x")) is True
        registry.clear()
        assert registry.get_registered_tool("console") is None


# ---------------------------------------------------------------------------
# console_triggers_self_interrupt：与 _handle_reload 的口径对齐
# ---------------------------------------------------------------------------

class TestConsolePredicate:
    """#165 删 confirm 闸门后：判定口径从「reload + 有效 token」变成「命令是 reload」。"""

    def test_reload_always_interrupts(self):
        """`reload` 无条件自中断（#165 D 删闸门后不再依赖 token）。

        为什么这条必须精确：如果这里返回 False，`reload_team` 的取消就没有
        `SELF_INTERRUPT` 标记，重启后会被重放成**第二次重载**；如果非 reload 命令
        返回 True，`dept ls` 这种正常返回的命令会留下中断标记，下次停 INIT 时被
        当成"已执行过"自动判成功 —— 一次「没重载」被永久记成「重载成功」。
        """
        assert console_triggers_self_interrupt(_call("reload")) is True

    def test_reload_with_leftover_confirm_syntax_does_not_interrupt(self):
        """带已被删除的 `--confirm` 写法 → 解析期就报 unknown_option，判定器返回 False。

        防止有人把 `--confirm` 当成「合法但被忽略」：它现在是未知选项，命令直接
        失败，因此谈不上中断。返回 False 才是正确口径（不回退到"反正要 reload"）。
        """
        assert console_triggers_self_interrupt(_call("reload --confirm deadbeef")) is False

    @pytest.mark.parametrize("command", ["dept ls", "help", "agent ls", "time get", "dept rm root"])
    def test_non_reload_commands_never_interrupt(self, command):
        """非 reload 命令一律不自中断（防止静态标志被误设）。"""
        assert console_triggers_self_interrupt(_call(command)) is False

    @pytest.mark.parametrize(
        "bad_call",
        [
            OpenAIToolCall(id="c", function={"name": "console", "arguments": "not json"}),
            OpenAIToolCall(id="c", function={"name": "console", "arguments": "{}"}),
            OpenAIToolCall(id="c", function={"name": "console", "arguments": '{"command": 123}'}),
            OpenAIToolCall(id="c", function={"name": "console", "arguments": '{"command": "dept"}'}),
            OpenAIToolCall(id="c", function={"name": "console", "arguments": '{"command": "blah"}'}),
            OpenAIToolCall(id="c", function={"name": "console", "arguments": '{"command": "dept ls --x"}'}),
        ],
    )
    def test_malformed_args_never_raise_and_never_interrupt(self, bad_call):
        """判定器在 turn runner 的调用链上：**任何畸形参数都只能返回 False，不能抛异常**。

        抛异常会把一次普通的管理命令变成整个 turn 崩溃，代价远大于漏判。
        """
        assert console_triggers_self_interrupt(bad_call) is False
