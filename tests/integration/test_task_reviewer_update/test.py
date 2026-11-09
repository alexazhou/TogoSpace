"""#158 集成：`update_task` 修改/撤销验收人（真库 + 工具层）。

为什么单列一类：单元用例用 DAL 替身证明「权限与顺序」，证明不了这几件事——
1. 主表 `reviewer_id` 是否**真的**落库（含 R2 的「状态没变、验收人也没变」）；
2. 事件原始行是否 `from_status`/`to_status` 均 NULL、`actor_id` 是否调用者、`content` 是否带新旧 ID；
3. 事务是否真回滚：事件写入抛异常时 `reviewer_id` 必须恢复原值（不能出现「改了但时间线没记录」）；
4. 工具层 `tools.update_task` 的两个新参数是否真的透传到 service（含互斥校验）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.taskService as taskService
from constants import EmployStatus, TaskStatus
from dal.db import (
    gtAgentManager,
    gtAgentTaskEventManager,
    gtAgentTaskManager,
    gtRoleTemplateManager,
    gtTeamManager,
)
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service.funcToolService import tools
from service.roomService.core import ToolCallContext
from tests.base import ServiceTestCase
from tests.integration.task_helpers import create_task_with_event

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestTaskReviewerUpdate(ServiceTestCase):
    """真库：改验收人 = 主表 + 一条 (NULL,NULL) 事件 + 事务性。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name="reviewer_update_team"))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="reviewer_update_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="creator", role_template_id=template.id),
                GtAgent(team_id=team.id, name="worker", role_template_id=template.id),
                GtAgent(team_id=team.id, name="reviewer_a", role_template_id=template.id),
                GtAgent(team_id=team.id, name="reviewer_b", role_template_id=template.id),
                GtAgent(team_id=team.id, name="outsider", role_template_id=template.id),
                GtAgent(
                    team_id=team.id, name="retired", role_template_id=template.id,
                    employ_status=EmployStatus.OFF_BOARD,
                ),
            ],
        )
        cls.agents = {a.name: a for a in await gtAgentManager.get_team_all_agents(team.id)}

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def _id(self, name: str) -> int:
        return self.agents[name].id

    async def _make_task(
        self, *, status: TaskStatus = TaskStatus.IN_PROGRESS, reviewer: str | None = "reviewer_a",
    ) -> GtAgentTask:
        return await create_task_with_event(
            GtAgentTask(
                team_id=self.team_id,
                title="reviewer update",
                assignee_id=self._id("worker"),
                creator_id=self._id("creator"),
                reviewer_id=None if reviewer is None else self._id(reviewer),
                status=status,
            )
        )

    async def _events(self, task_id: int) -> list:
        return await gtAgentTaskEventManager.list_events(task_id, limit=100)

    # ---------------- R1：权限在真库上咬合 ----------------

    async def test_creator_change_persists_and_appends_null_status_event(self):
        """创建人改验收人 ⇒ 主表落库 + 事件 (NULL,NULL)、actor=创建人、文案含新旧 ID 与名字。"""
        task = await self._make_task()
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("creator"),
            task_id=task.id,
            reviewer_id=self._id("reviewer_b"),
            note="甲休假，交给乙",
        )

        assert res["success"] is True
        assert res["action"] == "reviewer_updated"

        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.reviewer_id == self._id("reviewer_b")
        assert reloaded.status == TaskStatus.IN_PROGRESS  # 状态没被顺手改

        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1
        event = (await self._events(task.id))[0]
        assert event.from_status is None and event.to_status is None
        assert event.actor_id == self._id("creator")
        assert event.content.startswith("验收人变更：")
        assert str(self._id("reviewer_a")) in event.content
        assert str(self._id("reviewer_b")) in event.content
        assert "reviewer_a" in event.content and "reviewer_b" in event.content
        assert "甲休假，交给乙" in event.content

    async def test_current_reviewer_handover_in_reviewing(self):
        """REVIEWING 时当前验收人把验收人交接给别人（主表 + 事件都变，任务仍 REVIEWING）。"""
        task = await self._make_task(status=TaskStatus.REVIEWING)
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("reviewer_a"),
            task_id=task.id,
            reviewer_id=self._id("reviewer_b"),
        )

        assert res["success"] is True
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.reviewer_id == self._id("reviewer_b")
        assert reloaded.status == TaskStatus.REVIEWING
        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1

    async def test_assignee_cannot_change_reviewer_in_db(self):
        """执行人改验收人 ⇒ 拒绝，DB 未变、事件数未增（R1 变异靶点）。"""
        task = await self._make_task()
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("worker"),
            task_id=task.id,
            reviewer_id=self._id("reviewer_b"),
        )

        assert res["success"] is False
        assert res["error_code"] == "permission_denied"
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.reviewer_id == self._id("reviewer_a")
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    async def test_unrelated_member_cannot_change_reviewer_in_db(self):
        task = await self._make_task()
        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("outsider"),
            task_id=task.id,
            reviewer_id=self._id("reviewer_b"),
        )

        assert res["success"] is False
        assert res["error_code"] == "permission_denied"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == self._id("reviewer_a")

    # ---------------- R2：组合调用绕不过验收 ----------------

    async def test_transition_plus_reviewer_change_is_rejected_and_db_untouched(self):
        """IN_PROGRESS + 一次调用同时「提交 REVIEWING + 换成自己人」⇒ 拒绝，DB 状态与验收人都没变。"""
        task = await self._make_task()
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("worker"),
            task_id=task.id,
            new_status="REVIEWING",
            reviewer_id=self._id("worker"),
        )

        assert res["success"] is False
        assert res["error_code"] == "reviewer_change_must_be_separate"
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.status == TaskStatus.IN_PROGRESS
        assert reloaded.reviewer_id == self._id("reviewer_a")
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    # ---------------- R3：终态挡住 ----------------

    @pytest.mark.parametrize("terminal", ["DONE", "CANCELLED"])
    async def test_terminal_task_reviewer_immutable_in_db(self, terminal):
        task = await self._make_task()
        task.status = TaskStatus.value_of(terminal)
        await gtAgentTaskManager.save_task(task, only=[GtAgentTask.status])
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("creator"),
            task_id=task.id,
            reviewer_id=self._id("reviewer_b"),
        )

        assert res["success"] is False
        assert res["error_code"] == "invalid_transition"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == self._id("reviewer_a")
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    # ---------------- R4：REVIEWING 撤销被拒 ----------------

    async def test_clear_fields_in_reviewing_leaves_task_reviewable(self):
        task = await self._make_task(status=TaskStatus.REVIEWING)
        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id, clear_fields=["reviewer_id"],
        )

        assert res["success"] is False
        assert res["error_code"] == "reviewer_required_in_reviewing"
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.reviewer_id == self._id("reviewer_a")  # 没有变成「无人验收」死锁态

        # 死锁反证：验收人还在，仍然能通过验收
        done = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("reviewer_a"), task_id=task.id, new_status="DONE",
        )
        assert done["success"] is True

    async def test_clear_fields_by_creator_persists_null_and_warns(self):
        """创建人撤销 ⇒ DB `reviewer_id` 为 NULL、事件「验收人撤销：… → 无」、message 告知后果。"""
        task = await self._make_task()
        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            clear_fields=["reviewer_id"], note="不需要验收了",
        )

        assert res["success"] is True
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id is None
        event = (await self._events(task.id))[0]
        assert event.from_status is None and event.to_status is None
        assert event.content.startswith("验收人撤销：")
        assert "无" in event.content and "不需要验收了" in event.content
        assert "DONE" in res["message"]

        # 撤销后执行人可直接 DONE（既有语义，D2 说的「自己承担后果」）
        direct = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("worker"), task_id=task.id, new_status="DONE",
        )
        assert direct["success"] is True

    # ---------------- R5：新验收人校验 ----------------

    async def test_off_board_agent_rejected_in_db(self):
        task = await self._make_task()
        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            reviewer_id=self._id("retired"),
        )

        assert res["success"] is False
        assert res["error_code"] == "invalid_reviewer"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == self._id("reviewer_a")

    async def test_agent_from_other_team_rejected(self):
        other = await gtTeamManager.save_team(GtTeam(name="reviewer_update_other_team"))
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="reviewer_update_other_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            other.id, [GtAgent(team_id=other.id, name="foreigner", role_template_id=template.id)]
        )
        foreign = (await gtAgentManager.get_team_all_agents(other.id))[0]

        task = await self._make_task()
        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            reviewer_id=foreign.id,
        )

        assert res["success"] is False
        assert res["error_code"] == "invalid_reviewer"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == self._id("reviewer_a")

    async def test_reviewer_cannot_be_assignee_in_db(self):
        task = await self._make_task()
        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            reviewer_id=self._id("worker"),
        )

        assert res["success"] is False
        assert res["error_code"] == "invalid_reviewer"

    async def test_unchanged_reviewer_writes_no_event(self):
        task = await self._make_task()
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            reviewer_id=self._id("reviewer_a"),
        )

        assert res["success"] is True
        assert res["action"] == "reviewer_unchanged"
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    # ---------------- R8：事务性（真回滚） ----------------

    async def test_event_failure_rolls_back_reviewer_id(self):
        """事件写入抛异常 ⇒ 主表 `reviewer_id` 必须回滚，不能「改了但时间线没记录」。"""
        task = await self._make_task()
        original = self._id("reviewer_a")

        with patch.object(
            gtAgentTaskEventManager, "append_event", side_effect=RuntimeError("append failed"),
        ):
            with pytest.raises(RuntimeError):
                await taskService.update_task(
                    team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
                    reviewer_id=self._id("reviewer_b"),
                )

        gtAgentManager.clear_agent_cache()
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.reviewer_id == original

    # ---------------- 工具层透传 ----------------

    def _ctx(self, name: str) -> ToolCallContext:
        return ToolCallContext(agent_id=self._id(name), team_id=self.team_id, tool_name="update_task")

    async def test_tool_forwards_reviewer_id(self):
        task = await self._make_task()
        res = await tools.update_task(
            task_id=task.id, reviewer_id=self._id("reviewer_b"),
            note="工具层透传", _context=self._ctx("creator"),
        )

        assert res["success"] is True
        assert res["action"] == "reviewer_updated"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == self._id("reviewer_b")

    async def test_tool_forwards_clear_fields_only(self):
        task = await self._make_task()
        res = await tools.update_task(
            task_id=task.id, clear_fields=["reviewer_id"], _context=self._ctx("creator"),
        )

        assert res["success"] is True
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id is None

    async def test_tool_rejects_both_params(self):
        task = await self._make_task()
        res = await tools.update_task(
            task_id=task.id, reviewer_id=self._id("reviewer_b"), clear_fields=["reviewer_id"],
            _context=self._ctx("creator"),
        )

        assert res["success"] is False
        assert res["error_code"] == "ambiguous_reviewer_change"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == self._id("reviewer_a")

    async def test_tool_omitting_both_params_keeps_comment_flow(self):
        """回归底线（真库）：两个新参数都不传 ⇒ 仍是评论，主表 `reviewer_id` 与状态都不动。"""
        task = await self._make_task()
        res = await tools.update_task(
            task_id=task.id, note="只是评论", _context=self._ctx("outsider"),
        )

        assert res["success"] is True
        assert res["action"] == "commented"
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.reviewer_id == self._id("reviewer_a")
        assert reloaded.status == TaskStatus.IN_PROGRESS
        event = (await self._events(task.id))[0]
        assert event.content == "只是评论"
        assert event.from_status is None and event.to_status is None

    # ---------------- #181：clear_fields 白名单（真库 + 工具层） ----------------

    async def test_clear_fields_reviewer_id_persists_and_appends_one_event(self):
        """`clear_fields=["reviewer_id"]` ⇒ 主表 `reviewer_id` 真置 NULL + 恰好一条 (NULL,NULL) 事件。"""
        task = await self._make_task()
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            clear_fields=["reviewer_id"],
        )

        assert res["success"] is True
        assert res["action"] == "reviewer_updated"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id is None
        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1
        event = (await self._events(task.id))[0]
        assert event.from_status is None and event.to_status is None
        assert event.actor_id == self._id("creator")
        assert event.content.startswith("验收人撤销：")

    async def test_empty_clear_fields_keeps_reviewer_in_db(self):
        """`clear_fields=[]` ⇒ 验收人在库里保持原值（不被当成"撤销成功"）。"""
        task = await self._make_task()
        original = self._id("reviewer_a")
        events_before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            note="空数组不撤销", clear_fields=[],
        )

        assert res["success"] is True
        assert res["action"] == "commented"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == original
        assert await gtAgentTaskEventManager.count_events(task.id) == events_before + 1
        event = (await self._events(task.id))[0]
        assert event.content == "空数组不撤销"

    async def test_tool_forwards_clear_fields(self):
        """工具层透传（真入口）：`tools.update_task(clear_fields=["reviewer_id"])` ⇒ 库里置空。"""
        task = await self._make_task()
        res = await tools.update_task(
            task_id=task.id, clear_fields=["reviewer_id"], _context=self._ctx("creator"),
        )

        assert res["success"] is True
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id is None

    async def test_tool_rejects_invalid_clear_field(self):
        """工具层真实返回野值拦截结果（**运行时校验**，不是靠 schema enum）。

        这条是「schema 只产声明不产校验」的行为证据：直接调真入口传野值，
        断言返回体里 `success=False` + `error_code="invalid_clear_field"`，且库里没动。
        """
        task = await self._make_task()
        original = self._id("reviewer_a")

        res = await tools.update_task(
            task_id=task.id, clear_fields=["title"], _context=self._ctx("creator"),  # type: ignore[list-item]
        )

        assert res["success"] is False
        assert res["error_code"] == "invalid_clear_field"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == original

    async def test_tool_rejects_reviewer_id_with_clear_fields(self):
        """工具层：`reviewer_id` + `clear_fields=["reviewer_id"]` ⇒ 冲突码，库里不改。"""
        task = await self._make_task()
        original = self._id("reviewer_a")

        res = await tools.update_task(
            task_id=task.id, reviewer_id=self._id("reviewer_b"),
            clear_fields=["reviewer_id"], _context=self._ctx("creator"),
        )

        assert res["success"] is False
        assert res["error_code"] == "ambiguous_reviewer_change"
        assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == original

    # ---------------- #181：非字符串输入收口（真库） ----------------

    async def test_tool_rejects_non_string_clear_fields_in_db(self):
        """非字符串 `clear_fields` ⇒ `invalid_clear_field`，**库里 `reviewer_id` 原值不动**。

        最关键的是 dict 那条：收口前 `{"reviewer_id": True}` 会静默撤销验收人
        （dict 可迭代 ⇒ keys ⇒ 命中白名单）。真库上确认「没被撤销」比单测的替身更有说服力。
        """
        for bad_value in (5, {"reviewer_id": True}, b"reviewer_id"):
            task = await self._make_task()
            original = self._id("reviewer_a")
            events_before = await gtAgentTaskEventManager.count_events(task.id)

            res = await tools.update_task(
                task_id=task.id, clear_fields=bad_value, _context=self._ctx("creator"),  # type: ignore[arg-type]
            )

            assert res["success"] is False
            assert res["error_code"] == "invalid_clear_field", f"输入 {bad_value!r} 未被类型收口拦下"
            assert (await gtAgentTaskManager.get_task(task.id)).reviewer_id == original, (
                f"输入 {bad_value!r} 竟然真的改了库 —— 收口失效"
            )
            assert await gtAgentTaskEventManager.count_events(task.id) == events_before
