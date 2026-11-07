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
from service.funcToolService import consoleParsing as cp
from service.funcToolService.consoleTool import console_triggers_self_interrupt
from util.llmApiUtil import OpenAIToolCall


def _call(command: str) -> OpenAIToolCall:
    return OpenAIToolCall(
        id="c1",
        function={"name": "console", "arguments": json.dumps({"command": command}, ensure_ascii=False)},
    )


@pytest.fixture(autouse=True)
def _clean_tokens():
    cp._reset_confirm_tokens()
    yield
    cp._reset_confirm_tokens()


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
    def test_reload_without_token_does_not_interrupt(self):
        """不带 `--confirm` 的 reload 只返回 confirm_required，**不中断**。

        如果这里返回 True，历史条目会带上 `SELF_INTERRUPT` 标记，而实际什么都没执行。
        下次该条目因别的原因停在 INIT 时，会被 `_advance_step` 当成「已执行过」自动
        判成功 —— 一次「没重载」被永久记成「重载成功」。
        """
        assert console_triggers_self_interrupt(_call("reload")) is False

    def test_reload_with_valid_token_interrupts(self):
        """带**有效** token 的 reload 才自中断（这是 D6 的核心语义）。"""
        token = cp._issue_confirm_token(
            command_key="reload", target="reload", agent_id=1, team_id=2, impact=["x"]
        )
        assert console_triggers_self_interrupt(_call(f"reload --confirm {token}")) is True

    def test_reload_with_expired_token_does_not_interrupt(self):
        """过期 token → 不中断（`_handle_reload` 会拒绝，也不该留标记）。"""
        token = cp._issue_confirm_token(
            command_key="reload", target="reload", agent_id=1, team_id=2, impact=["x"]
        )
        cp._confirm_tokens[token]["expires_at"] = 0
        assert console_triggers_self_interrupt(_call(f"reload --confirm {token}")) is False

    def test_reload_with_unknown_token_does_not_interrupt(self):
        """编造的 token → 不中断。"""
        assert console_triggers_self_interrupt(_call("reload --confirm nope")) is False

    def test_token_for_other_command_does_not_interrupt(self):
        """别的命令发的 token 不能触发 reload 的中断（绑定 command_key）。"""
        token = cp._issue_confirm_token(
            command_key="room rm", target="x", agent_id=1, team_id=2, impact=["x"]
        )
        assert console_triggers_self_interrupt(_call(f"reload --confirm {token}")) is False

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
