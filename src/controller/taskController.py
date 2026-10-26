"""任务（协作任务）只读接口。

面向 Web 前端：任务详情 + 事件时间线。

**与工具层的区别**：`content` 一律**不截断**（`truncate_content=False`）。
100 字符截断是面向 Agent 的摘要口径（Agent 可用 `get_task_event` 取全文），
而 UI 时间线需要完整原文，且必须与 `TASK_EVENT_APPENDED` 推送内容保持一致。
"""
from controller.baseController import BaseHandler
from dal.db import gtAgentTaskManager
from service import taskService
from util import assertUtil


class TaskDetailHandler(BaseHandler):
    """GET /tasks/<task_id>.json - 任务详情（含依赖摘要与时间线首屏）"""

    async def get(self, task_id_str: str) -> None:
        task_id = int(task_id_str)
        task = await gtAgentTaskManager.get_task(task_id)
        assertUtil.assertNotNull(
            task,
            error_message=f"Task ID '{task_id}' not found",
            error_code="task_not_found",
        )

        self.return_json(
            await taskService.get_task(
                team_id=task.team_id,
                task_id=task_id,
                truncate_content=False,
            )
        )


class TaskEventsHandler(BaseHandler):
    """GET /tasks/<task_id>/events.json - 任务事件时间线（游标分页，最新在前）"""

    async def get(self, task_id_str: str) -> None:
        task_id = int(task_id_str)
        task = await gtAgentTaskManager.get_task(task_id)
        assertUtil.assertNotNull(
            task,
            error_message=f"Task ID '{task_id}' not found",
            error_code="task_not_found",
        )

        limit = max(1, min(int(self.get_query_argument("limit", "20")), 200))
        before_id_raw = self.get_query_argument("before_id", None)
        before_id = int(before_id_raw) if before_id_raw not in (None, "") else None

        self.return_json(
            await taskService.list_task_events(
                team_id=task.team_id,
                task_id=task_id,
                limit=limit,
                before_id=before_id,
                truncate_content=False,
            )
        )
