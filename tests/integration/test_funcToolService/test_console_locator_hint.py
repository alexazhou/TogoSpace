"""#220 A7 定位参数改写示例的**真入口**判据（单元层见 test_console_locator_hints.py）。

为什么要集成层：改写示例的价值在「模型照着提示改写后真的能走通」——
`dept get --dept-id 7` 收到的提示里的 `dept get 7` 必须是一条**可执行的命令**，
且 `dept get <id>` / `dept get <名称>`（D8 双定位）照旧走通（M3：报错不误伤合法
位置调用）。这三条都必须从唯一执行入口（AgentToolRegistry）走到 DB 才算数。

边界（说清楚、不放宽）：`--dept-id 7` 依旧被拒，只是拒得让人知道怎么改。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import service.ormService as ormService
from dal.db import gtAgentManager, gtDeptManager, gtRoleTemplateManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service import funcToolService
from service.agentService.toolRegistry import AgentToolRegistry, build_runtime_allow_specs
from service.funcToolService import core as func_tool_core
from service.roomService import ToolCallContext
from util.llmApiUtil import OpenAIToolCall

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

TEAM = "console_locator_hint_team"


def _tool_call(command: str) -> OpenAIToolCall:
    return OpenAIToolCall(
        id="call_1",
        function={"name": "console", "arguments": json.dumps({"command": command}, ensure_ascii=False)},
    )


def _build_registry() -> AgentToolRegistry:
    """与生产 driver 同形的注册表（照抄 `test_console_task._build_registry`）。"""
    registry = AgentToolRegistry()
    for name, func_tool in func_tool_core.load_func_tools().items():
        registry.register(
            func_tool.to_openai_tool(),
            funcToolService.run_tool_call,
            marks_turn_finish=name == "finish_action",
            self_interrupt=name == "reload_team",
            self_interrupt_predicate=func_tool_core.get_self_interrupt_predicate(name),
        )
    registry.apply_tool_allow_specs(build_runtime_allow_specs(None, is_root_leader=True))
    return registry


class TestConsoleLocatorHint(ServiceTestCase):
    """A7 改写示例从真 console 入口到 DB 的端到端行为（#220 判据 1 + M3 回归）。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="locator_hint_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [GtAgent(team_id=team.id, name="owner", role_template_id=template.id)],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.ids = {a.name: a.id for a in agents}
        cls.registry = _build_registry()

        dept = await gtDeptManager.save_dept(
            team.id, "hint_root", "A7 定位判据", None, cls.ids["owner"], [cls.ids["owner"]]
        )
        cls.dept_id = dept.id

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def _ctx(self) -> ToolCallContext:
        return ToolCallContext(agent_id=self.ids["owner"], team_id=self.team_id, chat_room=None)

    async def _call(self, command: str) -> dict:
        result = await self.registry.execute_tool_call(_tool_call(command), self._ctx())
        return result.result

    # ==================================================================
    # 判据 1：真入口 —— 选项形态被拒但给出**可执行**的改写
    # ==================================================================

    async def test_dept_get_with_locator_option_gets_executable_rewrite(self):
        """`dept get --dept-id <真实id>` → invalid_argument_count + 含改写示例。

        改写示例里写的就是本部门真实 id：提示必须**可执行**，不是示意文案。
        """
        result = await self._call(f"dept get --dept_id {self.dept_id}")
        assert result["success"] is False, f"选项形态定位不该被放行：{result}"
        assert result["error_code"] == "invalid_argument_count", (
            f"错误码应沿用 invalid_argument_count，实际 {result.get('error_code')!r}"
        )
        message = result.get("message", "")
        assert f"dept get {self.dept_id}" in message, f"没给可执行的等价写法：{message!r}"
        assert "位置参数" in message, f"没说清定位只接受位置形态：{message!r}"

    async def test_dept_get_with_name_option_is_unknown(self):
        """`dept get --name <名称>` 不能当定位用（P0 变化 b：按名通道整体取消）。

        改前 `--name` 是 locator 的名字形态选项，会被拒并给出「改写成位置参数
        `dept get hint_root`」的提示。变化 b 之后**名字形态本身被取消**（`dept get`
        只收正整数 ID），所以这个选项连存在都不该存在——继续要求「给它一个改写示例」
        等于要求把按名定位加回来。这里改钉「选项不存在」+ 提示怎么查 ID。
        """
        # #323（D1=A）之后 `dept get` 声明层排除了 `name`（C-16 死选项）：
        # 它既不在 help 里，也不在解析白名单里 ⇒ 解析阶段就 `unknown_option`，
        # 不再走到「位置参数数量」那一步（裁剪发生在更前面）。
        result = await self._call("dept get --name hint_root")
        assert result["success"] is False, result
        assert result["error_code"] == "unknown_option", result
        assert "name" in result["message"], result["message"]

        # 对照：位置参数给足时**同样**拒（死选项不会被位置槽「救活」），
        # 证明它是被裁剪掉的，而不是只在缺位置参数时才报错。
        with_pos = await self._call(f"dept get {self.dept_id} --name 不存在的名字")
        assert with_pos["success"] is False, with_pos
        assert with_pos["error_code"] == "unknown_option", with_pos

        by_pos = await self._call("dept get hint_root")
        assert by_pos["success"] is False, by_pos
        assert by_pos["error_code"] == "invalid_target", by_pos
        assert "dept ls" in by_pos["message"], by_pos["message"]

    async def test_help_carries_positional_label_via_real_entry(self):
        """真入口 `help dept get`：位置参数单列一段，行名复用签名 token（#323 方案 B）。"""
        result = await self._call("help dept get")
        assert result["success"] is True, result
        detail = result.get("help", "")
        assert "位置参数：" in detail, f"真 help 输出没有「位置参数：」段：{detail!r}"
        section = detail.split("位置参数：", 1)[1]
        assert any(ln.startswith("  <id> ") for ln in section.splitlines()), (
            f"位置段里没有 `<id>` 行：{detail!r}"
        )
        assert "不接受 --dept-id 选项写法" in detail, detail

    # ==================================================================
    # M3 回归：报错不误伤合法位置调用（说清楚、不放宽，也不多拒）
    # ==================================================================

    async def test_positional_id_form_still_works(self):
        """`dept get <id>` 照旧走通（M3：改写逻辑不得把合法调用也拒掉）。"""
        result = await self._call(f"dept get {self.dept_id}")
        assert result["success"] is True, f"合法 id 定位被误拒：{result}"
        assert result["dept"]["dept_id"] == self.dept_id and result["dept"]["dept_name"] == "hint_root", (
            f"定位错位（没定位到目标部门）：{result}"
        )

    async def test_positional_name_form_is_rejected_with_id_hint(self):
        """`dept get <名称>` 被拒（变化 b：位置槽只收正整数 ID），并提示先 `dept ls` 取 ID。

        对应地，合**法**的位置调用是 `dept get <id>`——由上面
        `test_positional_id_form_still_works` 钉住（不误伤真调用）。
        """
        result = await self._call("dept get hint_root")
        assert result["success"] is False, result
        assert result["error_code"] == "invalid_target", result
        assert "dept ls" in result["message"], result["message"]

    async def test_plain_missing_target_error_has_no_hint(self):
        """纯缺参（没写 locator 选项）报错不带改写（不无中生有），错误码不变。"""
        result = await self._call("dept get")
        assert result["success"] is False
        assert result["error_code"] == "invalid_argument_count"
        assert "等价写法" not in result.get("message", ""), (
            f"没写 locator 选项却给了改写：{result.get('message')!r}"
        )
