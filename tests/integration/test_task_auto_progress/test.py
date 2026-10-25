"""#68 集成：系统自动推进（依赖满足 PENDING→TODO）+ 认领置位（TODO→IN_PROGRESS）。

真库 + 服务层端到端：
  - 任务置 DONE 后，下游依赖全部满足的 PENDING 任务自动 → TODO，并写系统事件（actor=SYSTEM）；
  - 尚有未完成依赖时保持 PENDING；依赖全部完成后才推进；
  - 认领置位：assignee 认领 TODO 任务 → IN_PROGRESS（系统事件），幂等、非 assignee 跳过。

已知边界（显式登记、不静默）：系统推进 / 置位**仅改状态 + 记事件**，**不 publish `TASK_CHANGED`、不唤醒**
⇒ **不触发前端实时刷新**（前端需下次拉取方可见）；「实时刷新」归后续里程碑 D4（通知），本期不实现。
"""
import os
import sys

import pytest

import service.ormService as ormService
import service.taskService as taskService
from constants import SpecialAgent, TaskStatus
from dal.db import gtAgentTaskEventManager, gtAgentTaskManager
from model.dbModel.gtAgentTask import GtAgentTask
from tests.base import ServiceTestCase


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestTaskAutoProgress(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _create(self, *, assignee_id: int = 8, depends_on=None, status: str = "TODO") -> int:
        res = await taskService.create_task(
            team_id=1,
            creator_id=8,
            title="t",
            assignee_id=assignee_id,
            depends_on=depends_on,
        )
        assert res["success"] is True, res
        task_id = res["task_id"]
        task = await gtAgentTaskManager.get_task(task_id)
        target = TaskStatus.value_of(status)
        if task.status != target:
            # 直接置位以达到目标初始态（绕过状态机，仅用于造样本）
            task.status = target
            await gtAgentTaskManager.update_task(task, [GtAgentTask.status])
        return task_id

    async def _mark_done(self, task_id: int, caller_id: int = 8) -> None:
        res = await taskService.update_task(team_id=1, caller_id=caller_id, task_id=task_id, new_status="DONE")
        assert res["success"] is True, res

    # --------------------------------------------------------------- 依赖推进 ----
    async def test_auto_progress_advances_when_all_deps_done(self):
        a = await self._create()
        b = await self._create(depends_on=[a], status="PENDING")

        await self._mark_done(a)

        reloaded = await gtAgentTaskManager.get_task(b)
        assert reloaded.status == TaskStatus.TODO, "依赖全部完成 ⇒ B 应自动 PENDING→TODO"

        events = await gtAgentTaskEventManager.list_events(b, limit=10)
        assert events[0].from_status == TaskStatus.PENDING
        assert events[0].to_status == TaskStatus.TODO
        assert events[0].actor_id == SpecialAgent.SYSTEM.value, "系统自动推进需以 SYSTEM 身份记事件"
        assert "系统" in events[0].content

    async def test_auto_progress_holds_when_dep_unfinished(self):
        a1 = await self._create()
        a2 = await self._create()
        b = await self._create(depends_on=[a1, a2], status="PENDING")

        await self._mark_done(a1)
        assert (await gtAgentTaskManager.get_task(b)).status == TaskStatus.PENDING, "仍有未完成依赖 ⇒ 保持 PENDING"

        await self._mark_done(a2)
        assert (await gtAgentTaskManager.get_task(b)).status == TaskStatus.TODO, "依赖全部完成 ⇒ 推进 TODO"

    async def test_auto_progress_ignores_non_pending_dependents(self):
        """非 PENDING 的下游（如已 TODO/IN_PROGRESS）不被自动推进（只针对 PENDING）。"""
        a = await self._create()
        b = await self._create(depends_on=[a], status="TODO")  # 已是 TODO

        await self._mark_done(a)

        reloaded = await gtAgentTaskManager.get_task(b)
        assert reloaded.status == TaskStatus.TODO
        # 不应为 B 追加「PENDING→TODO」的自动推进事件
        events = await gtAgentTaskEventManager.list_events(b, limit=10)
        assert not any(e.from_status == TaskStatus.PENDING for e in events)

    # --------------------------------------------------------------- 认领置位 ----
    async def test_claim_task_sets_in_progress_with_system_event(self):
        t = await self._create(assignee_id=8)

        res = await taskService.claim_task(8, t)
        assert res == {"success": True, "action": "claimed"}

        reloaded = await gtAgentTaskManager.get_task(t)
        assert reloaded.status == TaskStatus.IN_PROGRESS

        events = await gtAgentTaskEventManager.list_events(t, limit=10)
        assert events[0].from_status == TaskStatus.TODO
        assert events[0].to_status == TaskStatus.IN_PROGRESS
        assert events[0].actor_id == SpecialAgent.SYSTEM.value

    async def test_claim_task_idempotent_when_already_in_progress(self):
        t = await self._create(assignee_id=8)
        await taskService.claim_task(8, t)

        again = await taskService.claim_task(8, t)
        assert again["action"] == "skipped"

        reloaded = await gtAgentTaskManager.get_task(t)
        assert reloaded.status == TaskStatus.IN_PROGRESS

    async def test_claim_task_skips_when_caller_not_assignee(self):
        t = await self._create(assignee_id=8)

        res = await taskService.claim_task(99, t)  # 非 assignee

        assert res["action"] == "skipped"
        assert (await gtAgentTaskManager.get_task(t)).status == TaskStatus.TODO
