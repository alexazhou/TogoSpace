"""#219 JSON 值保护的**真入口**判据（单元层见 test_console_json_value.py）。

## 为什么必须有集成层

形态判断的单测绿了不能证明「从 console 敲 `--member_names '["a","b"]'` 真的能把
JSON 数组送进 save_room」——转换发生在 `_option_args`（consoleTool），而上游解析、
路由、第 2 跳权限任何一处改动都可能让这条链静默断掉（console 工具历史事故）。

两条真入口判据：
  1. `room create --member_names '["alice","bob"]'` 走通：JSON 数组字面量经
     转换层变成 `["alice","bob"]`，成员真的进库；
  2. 成员不存在时报错点的是**真实成员名**（`ghost`），而不是被 CSV 拆坏的碎段
     （`["a`）——后者正是清单 §A1 的原始事故形态。

另加 D1（布尔末次显式值）的**真库三向**：A（单次 false）/ B（重复末次 false）/
C（裸 true）三个结果集合，A==B 且 A!=C。#218 原判据只断言 `success is True`、
没读回值 ⇒ 造不出「A/B/C 全同集」时判据恒真，零保护力；这里的集合断言
（A 含 DONE、C 不含）把「值真的生效」钉在库上。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import service.ormService as ormService
from dal.db import gtAgentManager, gtAgentTaskManager, gtRoomManager, gtRoleTemplateManager, gtTeamManager
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

TEAM = "console_json_value_team"


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


class TestConsoleJsonValueEntry(ServiceTestCase):
    """JSON 值保护从真 console 入口到 DB 的端到端行为（#219 判据 1 + D1）。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="console_json_role", soul="soul")
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

    def _ctx(self, name: str = "owner") -> ToolCallContext:
        return ToolCallContext(agent_id=self.ids[name], team_id=self.team_id, chat_room=None)

    async def _call(self, command: str) -> dict:
        result = await self.registry.execute_tool_call(_tool_call(command), self._ctx())
        return result.result

    async def _make_task(
        self,
        *,
        status,
        title: str,
        assignee: str = "bob",
    ) -> GtAgentTask:
        task = GtAgentTask(
            team_id=self.team_id,
            title=title,
            assignee_id=self.ids[assignee],
            creator_id=self.ids["owner"],
            status=status,
        )
        return await create_task_with_event(task)

    # ==================================================================
    # 判据 1：真入口 —— JSON 数组字面量从命令行走到 save_room
    # ==================================================================

    async def test_room_create_json_array_option_reaches_service(self):
        """`--member_names '["alice","bob"]'` 建房成功，两位成员真的进库。

        M1 变异（恢复无条件 CSV）下这命令必失败：`["a` / `"b"]` 两个碎段都不存在，
        save_room 报「未找到成员」——所以本条同时是 M1 的真入口级哨兵。
        """
        result = await self._call(
            "room create --name jv_room --member_names '[\"alice\",\"bob\"]'"
        )
        assert result["success"] is True, f"JSON 数组写法建房失败：{result}"
        row = await gtRoomManager.get_room_by_team_and_name(self.team_id, "jv_room")
        assert row is not None, "返回 success 但库里没建出房间"
        assert sorted(row.agent_ids) == sorted(
            [self.ids["alice"], self.ids["bob"]]
        ), f"成员没按 JSON 数组进入房间：{row.agent_ids!r}"

    async def test_room_create_json_bad_member_reports_the_real_name(self):
        """成员不存在时报错点的是**真实名字** `ghost`，不是 CSV 拆坏的碎段。

        事故形态（清单 §A1）：`["a` / `"b"]` 被当成成员名去查，报「未找到成员: ["a」，
        用户根本看不出错在哪。形态修复后报错必须落在 `ghost` 上。
        """
        result = await self._call(
            "room create --name jv_bad --member_names '[\"alice\",\"ghost\"]'"
        )
        assert result["success"] is False, f"含不存在成员竟成功：{result}"
        message = result.get("message", "")
        assert "ghost" in message, f"报错没点出真实的不存在成员：{message!r}"
        assert '["a' not in message and '"b' not in message, (
            f"报错里出现 CSV 拆坏后的碎段（形态保护没生效）：{message!r}"
        )

    # ==================================================================
    # D1：task ls 布尔开关的三向真库判据（A==B 且 A!=C）
    # ==================================================================

    async def test_task_ls_boolean_last_explicit_value_three_way(self):
        """A（单次 false）/ B（重复末次 false）/ C（裸 true）的集合断言。

        前置：同 assignee 的 TODO 与 DONE 任务**都存在**——不造 DONE，A/B/C 会
        返回同一个集合，任何断言都恒真（#218 判据盲区的教训）。

        - A == B：重复出现时**末次显式值** false 生效（变异「首次值生效」⇒ B 变成
          裸 true 语义 ⇒ B == C ⇒ 本条红）；
        - A != C：false 真的放进 open_only=False（含 DONE）。
        """
        from constants import TaskStatus

        done = await self._make_task(status=TaskStatus.DONE, title="jv-布尔三向-done")
        todo = await self._make_task(status=TaskStatus.TODO, title="jv-布尔三向-todo")

        a = await self._call(f"task ls --assignee {self.ids['bob']} --open_only=false")
        b = await self._call(f"task ls --assignee {self.ids['bob']} --open_only --open_only=false")
        c = await self._call(f"task ls --assignee {self.ids['bob']} --open_only")

        assert a["success"] is True and b["success"] is True and c["success"] is True, (
            f"三向调用有失败：A={a} B={b} C={c}"
        )
        ids_a = {t["id"] for t in a["tasks"]}
        ids_b = {t["id"] for t in b["tasks"]}
        ids_c = {t["id"] for t in c["tasks"]}
        assert done.id in ids_a, f"A（--open_only=false）应含已完结任务：{sorted(ids_a)}"
        assert todo.id in ids_a, f"A 应含未完结任务：{sorted(ids_a)}"
        assert ids_a == ids_b, (
            f"B（重复出现末次 false）应与 A 同集；A={sorted(ids_a)} B={sorted(ids_b)}"
            "（若 B==C，说明首次值生效的旧语义回来了）"
        )
        assert done.id not in ids_c, f"C（裸 --open_only）不应含已完结任务：{sorted(ids_c)}"
        assert ids_c != ids_a, "C 与 A 同集 ⇒ open_only 的值没生效，判据恒真"
