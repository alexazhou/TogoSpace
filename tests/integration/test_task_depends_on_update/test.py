"""#298 集成：`update_task` 支持创建人修改 `depends_on`（真库 + 工具层）。

为什么单列一类（单测替身证明不了的几件事）：
1. 主表 `depends_on` 是否**真的**落库（JSON 字段），以及「省略 = 不改 / `[]` = 清空」
   两条路径在真库上的实收差异；
2. 状态联动是否真写进 `status` 列（`PENDING→TODO`、`TODO→PENDING`、在途不变）；
3. 校验失败（自依赖 / 环 / 不存在 / 跨团队 / 终态）后**原依赖一字未变**（原子性），
   且没有多写一条事件；
4. 事件原始行：`from_status`/`to_status` 均 NULL、`actor_id` 是调用者、`content` 带旧→新；
5. 事务真回滚：事件写入抛异常时 `depends_on` 必须回到原值；
6. 工具层 `tools.update_task(depends_on=...)` 真的透传到 service（尤其 `[]` 不能被当成 `None`）。

真库口径：走 `ServiceTestCase` + 独立测试 DB，不 `rm /tmp/teamagent_tests_*.db`。

错误码口径（小马哥 2026-11-17 00:51）：新增一律 `task_depends_on_*` 前缀；
自依赖与直接环/间接环**合并**成 `task_depends_on_cycle`（原因写 message：`self` / `#a -> #b -> #a`）；
既有 `task_already_terminal` 等码一字不改（依赖变更走 `task_depends_on_terminal`）。
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


class TestTaskDependsOnUpdate(ServiceTestCase):
    """真库：改依赖 = 主表 + 一条 (NULL,NULL) 事件 + 状态联动 + 校验不留半改。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name="depends_on_update_team"))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="depends_on_update_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="creator", role_template_id=template.id),
                GtAgent(team_id=team.id, name="worker", role_template_id=template.id),
                GtAgent(team_id=team.id, name="outsider", role_template_id=template.id),
                GtAgent(
                    team_id=team.id, name="retired", role_template_id=template.id,
                    employ_status=EmployStatus.OFF_BOARD,
                ),
            ],
        )
        cls.agents = {a.name: a for a in await gtAgentManager.get_team_all_agents(team.id)}

        # 另一个团队（跨团队依赖必须被拒）
        other = await gtTeamManager.save_team(GtTeam(name="depends_on_update_other_team"))
        cls.other_team_id = other.id
        other_template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="depends_on_update_other_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            other.id,
            [GtAgent(team_id=other.id, name="foreigner", role_template_id=other_template.id)],
        )
        cls.foreign_agent = (await gtAgentManager.get_team_all_agents(other.id))[0]

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def _id(self, name: str) -> int:
        return self.agents[name].id

    def _ctx(self, name: str) -> ToolCallContext:
        return ToolCallContext(agent_id=self._id(name), team_id=self.team_id, tool_name="update_task")

    async def _make_task(
        self,
        *,
        status: TaskStatus = TaskStatus.TODO,
        depends_on: list[int] | None = None,
        title: str = "depends on update",
    ) -> GtAgentTask:
        return await create_task_with_event(
            GtAgentTask(
                team_id=self.team_id,
                title=title,
                assignee_id=self._id("worker"),
                creator_id=self._id("creator"),
                status=status,
                depends_on=list(depends_on or []),
            )
        )

    async def _events(self, task_id: int) -> list:
        return await gtAgentTaskEventManager.list_events(task_id, limit=100)

    async def _reload(self, task_id: int) -> GtAgentTask:
        reloaded = await gtAgentTaskManager.get_task(task_id)
        assert reloaded is not None
        return reloaded

    # ---------------- D1：创建人改依赖真的落库 + 一条 (NULL,NULL) 事件 ----------------

    async def test_creator_adds_unfinished_dep_persists_and_appends_event(self):
        """创建人给 TODO 任务挂一个未完成依赖 ⇒ 落库 + 回退 PENDING + 事件 (NULL,NULL)。

        「回退 PENDING」是我主动加的一处加固（TODO 尚未认领，否则 claim 能绕过依赖闸门），
        超出需求字面；若验收人判超范围，删掉 `_handle_depends_on_change` 里那 3 行即可。
        """
        dep = await self._make_task(status=TaskStatus.PENDING, title="被依赖的任务")
        task = await self._make_task(status=TaskStatus.TODO)
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id,
            caller_id=self._id("creator"),
            task_id=task.id,
            depends_on=[dep.id],
            note="真实依赖应是这条",
        )

        assert res["success"] is True
        assert res["action"] == "depends_on_updated"
        assert res["old_depends_on"] == []
        assert res["new_depends_on"] == [dep.id]
        assert res["old_status"] == "TODO"
        assert res["status"] == "PENDING"
        assert res["unfinished_depends_on"] == [dep.id]

        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == [dep.id]
        assert reloaded.status == TaskStatus.PENDING

        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1
        event = (await self._events(task.id))[0]
        assert event.from_status is None and event.to_status is None
        assert event.actor_id == self._id("creator")
        assert event.content.startswith("依赖变更：")
        assert f"#{dep.id}" in event.content
        assert "真实依赖应是这条" in event.content

    async def test_all_deps_done_promotes_pending_to_todo(self):
        """依赖全部已 DONE 且任务 PENDING ⇒ 转 TODO（与创建时推导一致）。"""
        dep = await self._make_task(status=TaskStatus.DONE, title="已完成的前置")
        task = await self._make_task(status=TaskStatus.PENDING)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[dep.id],
        )

        assert res["success"] is True
        assert res["old_status"] == "PENDING"
        assert res["status"] == "TODO"
        assert res["unfinished_depends_on"] == []
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == [dep.id]
        assert reloaded.status == TaskStatus.TODO

    async def test_todo_with_all_done_deps_is_not_regressed(self):
        """**反方向**（小马哥 01:32 指定）：TODO + 依赖已全部 DONE ⇒ 状态保持 TODO。

        加固「TODO + 未完成依赖 ⇒ 回退 PENDING」不许退化成「只要改依赖就回 PENDING」——
        少了这条，加固本身就是另一种错。
        """
        dep = await self._make_task(status=TaskStatus.DONE, title="已完成的前置")
        task = await self._make_task(status=TaskStatus.TODO)
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[dep.id], note="补记一条已完成的前置",
        )

        assert res["success"] is True
        assert res["action"] == "depends_on_updated"
        assert res["old_status"] == "TODO"
        assert res["status"] == "TODO", "TODO + 依赖全 DONE 被误回退成 PENDING"
        assert res["unfinished_depends_on"] == []
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == [dep.id]
        assert reloaded.status == TaskStatus.TODO
        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1

    async def test_explicit_empty_list_clears_deps_and_promotes_pending(self):
        """显式 `[]` = 清空依赖：主表真清空、PENDING→TODO、事件写「→ 无」。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="未完成的前置")
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[], note="前置任务取消，依赖清空",
        )

        assert res["success"] is True
        assert res["action"] == "depends_on_updated"
        assert res["old_depends_on"] == [dep.id]
        assert res["new_depends_on"] == []
        assert res["old_status"] == "PENDING"
        assert res["status"] == "TODO"

        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == []
        assert reloaded.status == TaskStatus.TODO
        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1
        event = (await self._events(task.id))[0]
        assert event.content.startswith("依赖变更：")
        assert "无" in event.content
        assert "前置任务取消，依赖清空" in event.content

    async def test_omitting_depends_on_is_not_a_clear(self):
        """省略参数 ≠ 清空：只传 note ⇒ 走评论分支，依赖与状态都不动（与上一条成对）。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="未完成的前置")
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("worker"), task_id=task.id,
            note="只是评论，不动依赖",
        )

        assert res["success"] is True
        assert res["action"] == "commented"
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == [dep.id]
        assert reloaded.status == TaskStatus.PENDING
        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1
        assert (await self._events(task.id))[0].content == "只是评论，不动依赖"

    async def test_duplicate_ids_are_deduped_in_db(self):
        """重复 ID 去重保序落库（`[x, x]` ⇒ `[x]`）。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[dep.id, dep.id],
        )

        assert res["success"] is True
        assert res["new_depends_on"] == [dep.id]
        assert (await self._reload(task.id)).depends_on == [dep.id]

    async def test_unchanged_deps_writes_no_event(self):
        """与现值一致的「改依赖」⇒ `depends_on_unchanged`，不写事件、状态不动。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[dep.id],
        )

        assert res["success"] is True
        assert res["action"] == "depends_on_unchanged"
        assert await gtAgentTaskEventManager.count_events(task.id) == before
        assert (await self._reload(task.id)).status == TaskStatus.PENDING

    # ---------------- D2：权限双向（创建人可改 / 非创建人明确码） ----------------

    async def test_assignee_cannot_change_deps(self):
        """执行人（被依赖闸门挡的那一方）不能自己拆闸门 ⇒ 明确错误码 + DB 一字未变。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("worker"), task_id=task.id,
            depends_on=[],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_not_creator"
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == [dep.id]
        assert reloaded.status == TaskStatus.PENDING
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    async def test_unrelated_member_cannot_change_deps(self):
        """同团队无关成员同样拒绝（不是「只有执行人不行」）。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("outsider"), task_id=task.id,
            depends_on=[],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_not_creator"
        assert (await self._reload(task.id)).depends_on == [dep.id]

    # ---------------- D3：校验（自依赖 / 环 / 不存在 / 跨团队 / 终态） ----------------

    async def test_self_dependency_rejected(self):
        """自依赖与直接环/间接环**共用一个码**（`task_depends_on_cycle`），区别只在 message 的 `self`。"""
        task = await self._make_task()
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[task.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_cycle"
        assert "self" in res["message"] and f"#{task.id}" in res["message"]
        assert (await self._reload(task.id)).depends_on == []
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    async def test_direct_cycle_rejected_and_original_deps_untouched(self):
        """直接环 a→b→a：拒绝，且 **a 的原依赖一字未变**（原子性）。"""
        a = await self._make_task(status=TaskStatus.PENDING, title="A")
        b = await self._make_task(status=TaskStatus.PENDING, depends_on=[a.id], title="B")
        before = await gtAgentTaskEventManager.count_events(a.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=a.id,
            depends_on=[b.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_cycle"
        assert f"#{a.id} -> #{b.id} -> #{a.id}" in res["message"], res["message"]
        assert (await self._reload(a.id)).depends_on == []
        assert (await self._reload(a.id)).status == TaskStatus.PENDING
        assert await gtAgentTaskEventManager.count_events(a.id) == before

    async def test_indirect_cycle_rejected_and_original_deps_untouched(self):
        """间接环 x→z→y→x：拒绝，x 原依赖保持原值（原值非空，证明没被「先清后拒」）。"""
        keeper = await self._make_task(status=TaskStatus.DONE, title="原有的依赖")
        x = await self._make_task(status=TaskStatus.PENDING, depends_on=[keeper.id], title="X")
        y = await self._make_task(status=TaskStatus.PENDING, depends_on=[x.id], title="Y")
        z = await self._make_task(status=TaskStatus.PENDING, depends_on=[y.id], title="Z")
        before = await gtAgentTaskEventManager.count_events(x.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=x.id,
            depends_on=[z.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_cycle"
        for tid in (x.id, y.id, z.id):
            assert f"#{tid}" in res["message"], f"环路径里缺 #{tid}：{res['message']}"
        reloaded = await self._reload(x.id)
        assert reloaded.depends_on == [keeper.id]  # 没被半改
        assert reloaded.status == TaskStatus.PENDING
        assert await gtAgentTaskEventManager.count_events(x.id) == before

    async def test_missing_dependency_rejected(self):
        task = await self._make_task()
        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[99999999],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_invalid_target"
        assert (await self._reload(task.id)).depends_on == []

    async def test_cross_team_dependency_rejected(self):
        """别的团队的任务不能当依赖（`get_tasks_by_ids` 不按 team 过滤，必须自校验）。"""
        foreign_task = await create_task_with_event(
            GtAgentTask(
                team_id=self.other_team_id,
                title="别队的任务",
                assignee_id=self.foreign_agent.id,
                creator_id=self.foreign_agent.id,
                status=TaskStatus.PENDING,
            )
        )
        task = await self._make_task()

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[foreign_task.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_invalid_target"
        assert (await self._reload(task.id)).depends_on == []

    @pytest.mark.parametrize("terminal", ["DONE", "CANCELLED"])
    async def test_terminal_task_deps_immutable(self, terminal):
        """已交付任务不允许事后篡改依赖（会改写「当时为什么能开工」的事实）。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task()
        task.status = TaskStatus.value_of(terminal)
        await gtAgentTaskManager.save_task(task, only=[GtAgentTask.status])
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[dep.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "task_depends_on_terminal"
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == []
        assert reloaded.status == TaskStatus.value_of(terminal)
        assert await gtAgentTaskEventManager.count_events(task.id) == before

    async def test_ambiguous_with_status_transition(self):
        """依赖变更与状态流转互斥（一次调用只做一件事）。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            new_status="TODO", depends_on=[dep.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "ambiguous_task_update"
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == []
        assert reloaded.status == TaskStatus.PENDING

    async def test_ambiguous_with_reviewer_change(self):
        """依赖变更与验收人变更同样互斥。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            reviewer_id=self._id("outsider"), depends_on=[dep.id],
        )

        assert res["success"] is False
        assert res["error_code"] == "ambiguous_task_update"
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == []
        assert reloaded.reviewer_id is None

    # ---------------- D4：状态联动（在途不回退 + 时间线如实记录） ----------------

    @pytest.mark.parametrize("inflight", ["IN_PROGRESS", "REVIEWING"])
    async def test_inflight_task_keeps_status_and_records_unfinished_dep(self, inflight):
        """在途任务加未完成依赖 ⇒ 状态**不变**，但事件里如实记录「当前存在未完成依赖」。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.value_of(inflight))
        before = await gtAgentTaskEventManager.count_events(task.id)

        res = await taskService.update_task(
            team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
            depends_on=[dep.id], note="补记真实依赖",
        )

        assert res["success"] is True
        assert res["old_status"] == inflight
        assert res["status"] == inflight
        assert res["unfinished_depends_on"] == [dep.id]

        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == [dep.id]
        assert reloaded.status == TaskStatus.value_of(inflight)
        assert await gtAgentTaskEventManager.count_events(task.id) == before + 1
        event = (await self._events(task.id))[0]
        assert event.from_status is None and event.to_status is None
        assert f"当前存在未完成依赖：[{dep.id}]" in event.content
        assert f"状态保持 {inflight}" in event.content

    # ---------------- D5：事务性（真回滚） ----------------

    async def test_event_failure_rolls_back_depends_on_and_status(self):
        """事件写入抛异常 ⇒ 依赖与状态都必须回滚，不能出现「改了但时间线没记录」。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING)

        with patch.object(
            gtAgentTaskEventManager, "append_event", side_effect=RuntimeError("append failed"),
        ):
            with pytest.raises(RuntimeError):
                await taskService.update_task(
                    team_id=self.team_id, caller_id=self._id("creator"), task_id=task.id,
                    depends_on=[dep.id],
                )

        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == []
        assert reloaded.status == TaskStatus.PENDING

    # ---------------- D6：工具层透传（真入口） ----------------

    async def test_tool_forwards_depends_on(self):
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING)

        res = await tools.update_task(
            task_id=task.id, depends_on=[dep.id], note="工具层透传",
            _context=self._ctx("creator"),
        )

        assert res["success"] is True
        assert res["action"] == "depends_on_updated"
        assert (await self._reload(task.id)).depends_on == [dep.id]

    async def test_tool_forwards_empty_list_as_clear(self):
        """工具层 `depends_on=[]` 必须被当成「清空」，**不能**被吞成「未提供」。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])

        res = await tools.update_task(
            task_id=task.id, depends_on=[], _context=self._ctx("creator"),
        )

        assert res["success"] is True
        assert res["action"] == "depends_on_updated"
        reloaded = await self._reload(task.id)
        assert reloaded.depends_on == []
        assert reloaded.status == TaskStatus.TODO

    async def test_tool_rejects_non_list_depends_on(self):
        """形态收口（真入口）：`"12"` 若被逐字符迭代会静默变成依赖 #1、#2，必须拒。"""
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置")
        for bad_value in (5, "12", {"3": True}, b"3", [True]):
            task = await self._make_task(status=TaskStatus.PENDING, depends_on=[dep.id])
            before = await gtAgentTaskEventManager.count_events(task.id)

            res = await tools.update_task(
                task_id=task.id, depends_on=bad_value, _context=self._ctx("creator"),  # type: ignore[arg-type]
            )

            assert res["success"] is False, f"输入 {bad_value!r} 未被拦下"
            assert res["error_code"] == "task_depends_on_invalid_input", f"输入 {bad_value!r} 错误码不对"
            reloaded = await self._reload(task.id)
            assert reloaded.depends_on == [dep.id], f"输入 {bad_value!r} 竟然真的改了库"
            assert await gtAgentTaskEventManager.count_events(task.id) == before

    # ---------------- D7：读侧立即反映新值（含已删除分支） ----------------

    async def test_get_task_detail_reflects_new_depends_on(self):
        dep = await self._make_task(status=TaskStatus.PENDING, title="前置任务甲")
        task = await self._make_task(status=TaskStatus.PENDING)

        await tools.update_task(
            task_id=task.id, depends_on=[dep.id], _context=self._ctx("creator"),
        )
        detail = await tools.get_task_detail(task_id=task.id, _context=self._ctx("creator"))

        assert detail["success"] is True
        assert detail["task"]["depends_on"] == [dep.id]
        assert detail["task"]["depends_on_details"] == [
            {"task_id": dep.id, "title": "前置任务甲", "status": "PENDING"}
        ]

    async def test_get_task_detail_marks_deleted_dependency(self):
        """依赖已不存在时 `depends_on_details` 走 `(已删除)` / `UNKNOWN` 分支。"""
        task = await self._make_task(status=TaskStatus.PENDING, depends_on=[99999999])

        detail = await tools.get_task_detail(task_id=task.id, _context=self._ctx("creator"))

        assert detail["success"] is True
        assert detail["task"]["depends_on"] == [99999999]
        assert detail["task"]["depends_on_details"] == [
            {"task_id": 99999999, "title": "(已删除)", "status": "UNKNOWN"}
        ]
