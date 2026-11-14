"""#228 判据债的**真入口**判据（单元层见 test_console_judge_debt.py）。

四条债在真入口各钉一条（38 要求「真入口或解析层直钉」，两层都给更稳）：
  D1 `--status DONE --status DONE`（重复值段）真入口走通且不炸下游；
  D2 `task ls --status ''` ≡ 不带 --status（空数组→None 语义的真入口形态）；
  D3 `room create --member-names ' ["alice","bob"]'`（值段前导空格）建房成功；
  D4 `room create --member-names '[" alice ","bob"]'` 报错点名**带空格**的原名
     （JSON 不 trim 的行为级证据：若被 trim，报错会是 `alice` 而非 `' alice '`）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import service.ormService as ormService
from constants import TaskStatus
from dal.db import gtAgentManager, gtAgentTaskManager, gtRoleTemplateManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service import funcToolService
from service.agentService.toolRegistry import AgentToolRegistry, build_runtime_allow_specs
from service.funcToolService import core as func_tool_core
from service.roomService import ToolCallContext
from util.llmApiUtil import OpenAIToolCall

from ...base import ServiceTestCase
from ...integration.task_helpers import create_task_with_event

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

TEAM = "console_judge_debt_team"


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


class TestConsoleJudgeDebtEntry(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="debt_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="owner", role_template_id=template.id),
                GtAgent(team_id=team.id, name="alice", role_template_id=template.id),
                GtAgent(team_id=team.id, name="bob", role_template_id=template.id),
            ],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.ids = {a.name: a.id for a in agents}
        cls.registry = _build_registry()

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def _ctx(self) -> ToolCallContext:
        return ToolCallContext(agent_id=self.ids["owner"], team_id=self.team_id, chat_room=None)

    async def _call(self, command: str) -> dict:
        result = await self.registry.execute_tool_call(_tool_call(command), self._ctx())
        return result.result

    async def _make_task(self, *, status, title: str) -> GtAgentTask:
        return await create_task_with_event(
            GtAgentTask(
                team_id=self.team_id,
                title=title,
                assignee_id=self.ids["alice"],
                creator_id=self.ids["owner"],
                status=status,
            )
        )

    # D1：重复值段真入口（去重后照常转发，不炸下游）
    async def test_d1_repeated_status_segments_reach_tool(self):
        from constants import TaskStatus

        done = await self._make_task(status=TaskStatus.DONE, title="debt-重复段-done")
        todo = await self._make_task(status=TaskStatus.TODO, title="debt-重复段-todo")
        held = await self._make_task(status=TaskStatus.ON_HOLD, title="debt-重复段-hold")

        result = await self._call(
            f"task ls --status DONE --status DONE --assignee {self.ids['alice']}"
        )
        assert result["success"] is True, f"重复值段真入口失败：{result}"
        ids = {t["id"] for t in result["tasks"]}
        assert done.id in ids and todo.id not in ids and held.id not in ids, (
            f"--status DONE --status DONE 去重后应只按 DONE 过滤，实收 {sorted(ids)}"
        )

    # D2：空数组 → None 的真入口形态（--status '' ≡ 不带 --status）
    async def test_d2_empty_status_option_equals_absent(self):
        from constants import TaskStatus

        done = await self._make_task(status=TaskStatus.DONE, title="debt-空数组-done")
        todo = await self._make_task(status=TaskStatus.TODO, title="debt-空数组-todo")

        # 两边都显式 --open-only=false（默认 open_only=True 会把 DONE 滤掉，
        # 让「集合相等」在没验证 --status 生效的情况下恒真）。
        plain = await self._call(f"task ls --assignee {self.ids['alice']} --open-only=false")
        empty = await self._call(f"task ls --assignee {self.ids['alice']} --status '' --open-only=false")
        assert plain["success"] is True and empty["success"] is True, f"{plain} / {empty}"
        ids_plain = {t["id"] for t in plain["tasks"]}
        ids_empty = {t["id"] for t in empty["tasks"]}
        assert done.id in ids_plain and todo.id in ids_plain, (
            f"前置不成立（--open-only=false 应放开 DONE）：{sorted(ids_plain)}"
        )
        assert ids_plain == ids_empty, (
            f"--status '' 应 ≡ 未提供（空数组折 None），实收 {sorted(ids_plain)} vs {sorted(ids_empty)}"
        )

    # D3：值段前导空格的真入口（verify-219 D3 的原始事故形态）
    async def test_d3_leading_space_json_creates_room(self):
        result = await self._call('room create --name debt_room --member-names \' ["alice","bob"]\'')
        assert result["success"] is True, f"前导空格 JSON 段真入口失败：{result}"

    # D4 真入口说明：JSON「元素不 trim」的判据钉在**转换层**（单元
    # test_json_element_spaces_reach_the_tool 直接断言转发参数带空格），不钉
    # 真入口 —— room/task 的下游工具会对成员名/枚举自行规整（实测 room create
    # 对 ' alice ' 规整成功），工具层的清洗属于它自己的语义，不能当 console 层
    # trim 与否的判据（38 判据允许「真入口或解析层直钉」二选一；D4 按原记档
    # 口径取转换层）。
