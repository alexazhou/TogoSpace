"""#221 A2 kebab-case 的**真入口**判据（单元层见 test_console_kebab_option.py）。

判据 1 要求双向：kebab 主形态走通 + 兼容期旧名仍走通。为什么必须真入口：
形态转换横跨 tokenizer → 白名单 → 布尔判定 → 类型表 → 归一 → 转发，任何一环
只认一种形态，单元层全绿也会在真入口断链（console 历史事故形态）。

边界：不改语义、不动 mcp2cli 上游；kebab 与旧名对同一命令必须**产出完全相同
的转发参数**（形态差异不许漏到下游）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import service.ormService as ormService
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

TEAM = "console_kebab_team"


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


class TestConsoleKebabEntry(ServiceTestCase):
    """kebab/旧名双向从真 console 入口到 DB（#221 判据 1）。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="kebab_role", soul="soul")
        )
        cls.role_template_id = template.id
        await gtAgentManager.batch_save_agents(
            team.id,
            [GtAgent(team_id=team.id, name="owner", role_template_id=template.id)],
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

    async def _make_task(
        self,
        *,
        title: str,
        assignee_id: int | None = None,
        reviewer_id: int | None = None,
        creator_id: int | None = None,
    ) -> GtAgentTask:
        task = GtAgentTask(
            team_id=self.team_id,
            title=title,
            assignee_id=assignee_id if assignee_id is not None else self.ids["owner"],
            creator_id=creator_id if creator_id is not None else self.ids["owner"],
            reviewer_id=reviewer_id,
        )
        return await create_task_with_event(task)

    # ==================================================================
    # 判据 1：双向走通（kebab 主形态 + 兼容期旧名）
    # ==================================================================

    async def test_kebab_option_reaches_tool(self):
        """`task ls --title-keyword 登录 --open-only`（全 kebab）走通。"""
        hit = await self._make_task(title="kebab-登录失败排查")
        await self._make_task(title="kebab-完全无关的标题")

        result = await self._call(
            f"task ls --title-keyword 登录失败排查 --assignee {self.ids['owner']} --open-only"
        )
        assert result["success"] is True, f"kebab 主形态真入口失败：{result}"
        ids = {t["id"] for t in result["tasks"]}
        assert ids == {hit.id}, f"--title-keyword 未生效，命中 {sorted(ids)}"

    async def test_legacy_option_still_reaches_tool(self):
        """`task ls --title_keyword 登录 --open_only`（兼容期旧名）仍走通。

        这是兼容期的意义：一个版本内旧写法不许断。M1（别名机制失效）下此条红。
        """
        hit = await self._make_task(title="legacy-登录成功记录")

        result = await self._call(
            f"task ls --title_keyword 登录成功 --assignee {self.ids['owner']} --open_only"
        )
        assert result["success"] is True, f"兼容期旧名真入口失败：{result}"
        ids = {t["id"] for t in result["tasks"]}
        assert ids == {hit.id}, f"--title_keyword 未生效，命中 {sorted(ids)}"

    async def test_both_forms_produce_identical_downstream_effect(self):
        """kebab 与旧名对**写操作**必须产出完全相同的库状态（形态差异不漏到下游）。

        M1/M2 任一转换错位，两边的 reviewer_id 不会一致。
        清除字段用 `reviewer_id`（ALLOWED_CLEAR_FIELDS 白名单成员；note 不在）。
        """
        await gtAgentManager.batch_save_agents(
            self.team_id,
            [
                GtAgent(team_id=self.team_id, name="kebab_worker",
                        role_template_id=self.role_template_id),
                GtAgent(team_id=self.team_id, name="kebab_reviewer",
                        role_template_id=self.role_template_id),
            ],
        )
        agents = await gtAgentManager.get_team_all_agents(self.team_id)
        worker_id = next(a.id for a in agents if a.name == "kebab_worker")
        reviewer_id = next(a.id for a in agents if a.name == "kebab_reviewer")
        # owner 是 creator（创建人才能清 reviewer_id，#218 权限规则）；
        # assignee=worker、reviewer=reviewer 三者互异。
        a = await self._make_task(
            title="kebab-写一致性", assignee_id=worker_id, reviewer_id=reviewer_id,
            creator_id=self.ids["owner"],
        )
        b = await self._make_task(
            title="kebab-写一致性", assignee_id=worker_id, reviewer_id=reviewer_id,
            creator_id=self.ids["owner"],
        )

        via_kebab = await self._call(f"task update {a.id} --remove-field reviewer_id")
        via_legacy = await self._call(f"task update {b.id} --remove_field reviewer_id")
        assert via_kebab["success"] is True and via_legacy["success"] is True, (
            f"kebab={via_kebab} legacy={via_legacy}"
        )
        row_a = await gtAgentTaskManager.get_task(a.id)
        row_b = await gtAgentTaskManager.get_task(b.id)
        assert row_a.reviewer_id is None and row_b.reviewer_id is None, (
            f"两形态的库结果不一致：kebab={row_a.reviewer_id!r} legacy={row_b.reviewer_id!r}"
        )

    async def test_help_via_real_entry_shows_kebab_only(self):
        """真入口 `help task ls`：只见 --title-keyword，不见 --title_keyword（M3）。"""
        result = await self._call("help task ls")
        assert result["success"] is True, result
        detail = result.get("help", "")
        assert "--title-keyword" in detail, f"真 help 没展示 kebab 主形态：{detail!r}"
        assert "--title_keyword" not in detail, f"真 help 混入旧名：{detail!r}"
