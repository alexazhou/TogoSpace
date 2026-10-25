"""#67 集成：任务工具面（`update_task` 内联评论 / `get_task_detail` / `list_task_events` / 去 `result`·`block_reason`）。

真库 + 工具层端到端：
  - 省略 `new_status` ⇒ 评论事件（from/to 均 NULL）、状态不变、action=commented；
  - 给定 `new_status` ⇒ 流转、action=transitioned；
  - 未知参数（旧字段 `result`）经 `run_tool_call` 直接报错（无兼容别名）；
  - `get_task_detail` 正常返回；`get_task` 保留为废弃别名；
  - `list_tasks` / `list_task_events` 工具签名与返回。
"""
import json
import os
import sys

import pytest

import service.ormService as ormService
from constants import TaskStatus
from dal.db import gtAgentTaskEventManager, gtAgentTaskManager
from model.dbModel.gtAgentTask import GtAgentTask
from service.funcToolService import core as funcCore
from service.funcToolService import tools
from service.roomService.core import ToolCallContext
from tests.base import ServiceTestCase


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestTaskTools(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def _ctx(self, *, tool_name: str = "") -> ToolCallContext:
        return ToolCallContext(agent_id=8, team_id=1, tool_name=tool_name)

    async def _make_task(self, *, status: TaskStatus = TaskStatus.TODO, reviewer_id=None) -> GtAgentTask:
        task = GtAgentTask(
            team_id=1,
            title="tool task",
            assignee_id=8,
            creator_id=8,
            reviewer_id=reviewer_id,
            status=status,
        )
        return await gtAgentTaskManager.create_task(task)

    async def test_update_task_comment_via_tool(self):
        """省略 new_status ⇒ 追加评论事件、状态不变、action=commented。"""
        task = await self._make_task()

        res = await tools.update_task(task_id=task.id, note="复测通过", _context=self._ctx())

        assert res["success"] is True
        assert res["action"] == "commented"
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.status == TaskStatus.TODO
        events = await gtAgentTaskEventManager.list_events(task.id, limit=10)
        assert events[0].from_status is None
        assert events[0].to_status is None
        assert events[0].content == "复测通过"
        assert events[0].actor_id == 8

    async def test_update_task_transition_via_tool(self):
        """给定 new_status ⇒ 流转（action=transitioned）。"""
        task = await self._make_task(reviewer_id=6)

        res = await tools.update_task(task_id=task.id, new_status="IN_PROGRESS", note="开工", _context=self._ctx())

        assert res["success"] is True
        assert res["action"] == "transitioned"
        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.status == TaskStatus.IN_PROGRESS

    async def test_run_tool_call_rejects_unknown_param(self):
        """旧字段 result 作为未知参数经 run_tool_call ⇒ 直接报错（无兼容别名）。"""
        task = await self._make_task()
        funcCore.load_func_tools()
        ctx = self._ctx(tool_name="update_task")

        res = await funcCore.run_tool_call(json.dumps({"task_id": task.id, "result": "x"}), ctx)

        assert res["success"] is False
        assert "result" in res["message"]

    async def test_get_task_detail_and_deprecated_alias(self):
        task = await self._make_task()

        res = await tools.get_task_detail(task_id=task.id, _context=self._ctx())
        assert res["success"] is True
        assert res["task"]["id"] == task.id
        assert "events" in res["task"]

        old = await tools.get_task(task_id=task.id, _context=self._ctx())
        assert old["success"] is True
        assert "deprecated" in old

    async def test_list_task_events_tool(self):
        """工具层 list_task_events 返回时间线（创建事件 + 评论）。"""
        task = await self._make_task()
        await tools.update_task(task_id=task.id, note="备注1", _context=self._ctx())

        res = await tools.list_task_events(task_id=task.id, _context=self._ctx())

        assert res["success"] is True
        assert len(res["events"]) >= 2

    async def test_list_task_events_total_is_cross_page(self):
        """F-a：`total` = 该任务事件总数（跨页）；`limit` 只截断本页（`count`）。"""
        task = await self._make_task()
        # 创建事件 1 条 + 3 条评论 = 4 条事件
        for i in range(3):
            await tools.update_task(task_id=task.id, note=f"备注{i}", _context=self._ctx())

        page1 = await tools.list_task_events(task_id=task.id, limit=2, _context=self._ctx())
        assert page1["total"] == 4  # 跨页总数
        assert page1["count"] == 2  # 本页条数（被 limit 截断）
        assert len(page1["events"]) == 2

        # 用游标向前翻页：total 不变，第二页返回剩余 2 条
        last_id = page1["events"][-1]["id"]
        page2 = await tools.list_task_events(
            task_id=task.id, limit=2, before_id=last_id, _context=self._ctx()
        )
        assert page2["total"] == 4
        assert page2["count"] == 2

    async def test_list_tasks_tool_named_params(self):
        """工具层 list_tasks 用 assignee/reviewer 参数。"""
        task = await self._make_task()

        res = await tools.list_tasks(assignee=8, _context=self._ctx())

        assert res["success"] is True
        assert any(t["id"] == task.id for t in res["tasks"])
