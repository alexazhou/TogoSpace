"""#66 集成测试：状态流转「同事务」写事件 + 事件读取（真库）。

覆盖骨架族 E：
  E1 每次成功流转必且仅一条流转事件（同生共死）；
  E2 事件流可唯一重放流转序列 + 每步注释；
  E3 只增不改（DAL 无 UPDATE/DELETE）；
  E4 NULL 组合：非NULL→NULL 落库前拒绝。
以及：游标分页、派生属性（最近一次说明 / 当前搁置原因 PIN-4）、事务回滚。
"""
import os
import sys

import pytest

import service.ormService as ormService
import service.taskService as taskService
from constants import EVENT_TRUNCATED_SUFFIX, TaskStatus
from dal.db import gtAgentTaskEventManager, gtAgentTaskManager
from model.dbModel.gtAgentTask import GtAgentTask
from tests.base import ServiceTestCase
from tests.integration.task_helpers import create_task_with_event, transition_task


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestTaskEvents(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _make_task(self, *, title: str, status: TaskStatus = TaskStatus.TODO) -> GtAgentTask:
        task = GtAgentTask(
            team_id=1,
            title=title,
            assignee_id=8,
            creator_id=8,
            status=status,
        )
        return await create_task_with_event(task)

    async def _events(self, task_id: int):
        return await gtAgentTaskEventManager.list_events(task_id, limit=100)

    # ------------------------------------------------------------------ E1 ----
    async def test_create_writes_single_create_event(self):
        """创建任务应在同事务写一条创建事件（from=NULL、to=初始状态、actor=creator）。"""
        task = await self._make_task(title="ev-create")

        events = await self._events(task.id)
        assert len(events) == 1
        e = events[0]
        assert e.actor_id == 8
        assert e.from_status is None
        assert e.to_status == TaskStatus.TODO
        assert e.content == ""

    async def test_transition_writes_one_event_and_updates_main(self):
        """一次成功流转应更新主表状态 + 仅一条流转事件（from≠to、actor=caller、content=note）。"""
        task = await self._make_task(title="ev-transition")

        await transition_task(
            task,
            actor_id=8,
            from_status=TaskStatus.TODO,
            to_status=TaskStatus.IN_PROGRESS,
            content="go",
        )

        events = await self._events(task.id)  # id DESC
        assert len(events) == 2
        assert (events[0].from_status, events[0].to_status) == (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
        assert events[0].actor_id == 8
        assert events[0].content == "go"
        assert (events[1].from_status, events[1].to_status) == (None, TaskStatus.TODO)

        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.status == TaskStatus.IN_PROGRESS

    async def test_create_is_atomic_rollback(self, monkeypatch):
        """创建事件写入失败 → 主表插入整体回滚（同生共死）。"""
        async def boom(*args, **kwargs):
            raise RuntimeError("event insert failed")

        monkeypatch.setattr(gtAgentTaskEventManager, "append_event", boom)

        task = GtAgentTask(team_id=1, title="ev-atomic-create", assignee_id=8, creator_id=8, status=TaskStatus.TODO)
        with pytest.raises(RuntimeError):
            await create_task_with_event(task)

        rows = list(await GtAgentTask.select().where(GtAgentTask.title == "ev-atomic-create").aio_execute())
        assert rows == [], "事件写入失败时主表不应留下任务行"

    async def test_transition_is_atomic_rollback(self, monkeypatch):
        """流转事件写入失败 → 主表状态更新回滚。"""
        task = await self._make_task(title="ev-atomic-update")

        async def boom(*args, **kwargs):
            raise RuntimeError("event insert failed")

        monkeypatch.setattr(gtAgentTaskEventManager, "append_event", boom)

        with pytest.raises(RuntimeError):
            await transition_task(
                task,
                actor_id=8,
                from_status=TaskStatus.TODO,
                to_status=TaskStatus.IN_PROGRESS,
                content="x",
            )

        reloaded = await gtAgentTaskManager.get_task(task.id)
        assert reloaded.status == TaskStatus.TODO, "事件写入失败时状态更新应回滚"
        assert len(await self._events(task.id)) == 1  # 只剩创建事件

    # ------------------------------------------------------------------ E2 ----
    async def test_event_stream_replays_transition_sequence(self):
        """事件流可唯一重放流转序列 + 每步注释。"""
        task = await self._make_task(title="ev-replay")
        seq = [
            (TaskStatus.TODO, TaskStatus.IN_PROGRESS, "start"),
            (TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD, "hold"),
            (TaskStatus.ON_HOLD, TaskStatus.IN_PROGRESS, "resume"),
        ]
        for f, t, c in seq:
            await transition_task(
                task, actor_id=8, from_status=f, to_status=t, content=c
            )

        events = await self._events(task.id)  # id DESC
        transitions = [e for e in events if e.from_status is not None]
        assert len(transitions) == 3
        replay = [(e.from_status, e.to_status, e.content) for e in reversed(transitions)]
        assert replay == seq

    # ------------------------------------------------------------------ E3 ----
    def test_dal_is_append_only(self):
        """DAL 只追加：不提供任何修改 / 删除接口。"""
        assert not hasattr(gtAgentTaskEventManager, "update_event")
        assert not hasattr(gtAgentTaskEventManager, "delete_event")

    # ------------------------------------------------------------------ E4 ----
    async def test_append_event_rejects_non_null_to_null(self):
        """非NULL→NULL 组合应在落库前拒绝。"""
        with pytest.raises(ValueError):
            await gtAgentTaskEventManager.append_event(
                999, 8, from_status=TaskStatus.TODO, to_status=None
            )

        assert await gtAgentTaskEventManager.list_events(999) == []

    async def test_append_event_rejects_self_loop(self):
        """PIN-1（F-b）：`from == to`（自环）应在落库前拒绝，不得记为流转事件。

        可红性：若移除 DAL 的 `from == to` 守卫，本用例不再抛 `ValueError` ⇒ 转红。
        """
        with pytest.raises(ValueError):
            await gtAgentTaskEventManager.append_event(
                998, 8, from_status=TaskStatus.ON_HOLD, to_status=TaskStatus.ON_HOLD
            )

        assert await gtAgentTaskEventManager.list_events(998) == []

    async def test_append_event_allows_legal_null_combinations(self):
        """其余三格合法：评论(NULL,NULL)、创建(NULL,非NULL)、流转(非NULL,非NULL)。"""
        task = await self._make_task(title="ev-null-combos")

        await gtAgentTaskEventManager.append_event(task.id, 8, from_status=None, to_status=None, content="comment")
        await gtAgentTaskEventManager.append_event(task.id, 8, from_status=None, to_status=TaskStatus.PENDING, content="")
        await gtAgentTaskEventManager.append_event(
            task.id, 8, from_status=TaskStatus.PENDING, to_status=TaskStatus.TODO, content="advance"
        )

        events = await self._events(task.id)
        assert len(events) == 4  # 创建 + 3

    # ------------------------------------------------------------- 游标分页 ----
    async def test_cursor_pagination_by_id(self):
        """按 id 游标分页（不使用 offset）。"""
        task = await self._make_task(title="ev-paging")
        for i in range(4):
            await gtAgentTaskEventManager.append_event(
                task.id, 8, from_status=None, to_status=TaskStatus.PENDING, content=f"n{i}"
            )

        all_ev = await self._events(task.id)
        assert len(all_ev) == 5

        page1 = await gtAgentTaskEventManager.list_events(task.id, limit=2)
        assert [e.id for e in page1] == [all_ev[0].id, all_ev[1].id]

        page2 = await gtAgentTaskEventManager.list_events(task.id, limit=2, before_id=page1[-1].id)
        assert [e.id for e in page2] == [all_ev[2].id, all_ev[3].id]

    # --------------------------------------------------------------- 派生属性 ----
    async def test_service_get_task_derived_props(self):
        """get_task 挂载时间线摘要 + 「最近一次说明」+「当前搁置原因」(PIN-4)。"""
        task = await self._make_task(title="ev-derived")
        await transition_task(
            task, actor_id=8, from_status=TaskStatus.TODO, to_status=TaskStatus.IN_PROGRESS, content="开工"
        )
        await transition_task(
            task, actor_id=8, from_status=TaskStatus.IN_PROGRESS, to_status=TaskStatus.ON_HOLD, content="等接口"
        )

        res = await taskService.get_task(team_id=1, task_id=task.id)
        assert res["success"] is True
        assert res["task"]["last_note"] == "等接口"
        assert res["task"]["current_block_reason"] == "等接口"

        # 恢复后：不再是 ON_HOLD → 当前搁置原因 None；最近一次说明 = 恢复注释
        await transition_task(
            task, actor_id=8, from_status=TaskStatus.ON_HOLD, to_status=TaskStatus.IN_PROGRESS, content="恢复"
        )
        res2 = await taskService.get_task(team_id=1, task_id=task.id)
        assert res2["task"]["current_block_reason"] is None
        assert res2["task"]["last_note"] == "恢复"

    async def test_service_list_task_events(self):
        """服务层事件读取：游标透传 + 降序返回。"""
        task = await self._make_task(title="ev-list")
        await transition_task(
            task, actor_id=8, from_status=TaskStatus.TODO, to_status=TaskStatus.IN_PROGRESS, content="go"
        )

        res = await taskService.list_task_events(team_id=1, task_id=task.id, limit=1)
        assert res["success"] is True
        # 本页条数（被 limit 截断）= 1；跨页总数（创建 + 流转）= 2（F-a 口径）
        assert res["count"] == 1
        assert res["total"] == 2
        assert res["events"][0]["to_status"] == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# Web 接口口径：时间线要完整原文（不截断），与 TASK_EVENT_APPENDED 推送一致
# ---------------------------------------------------------------------------


class TestTaskEventFullContent(ServiceTestCase):
    """`truncate_content=False`：Web 接口读到的 content 必须是完整原文。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _make_task(self, *, title: str) -> GtAgentTask:
        task = GtAgentTask(team_id=1, title=title, assignee_id=8, creator_id=8, status=TaskStatus.TODO)
        return await create_task_with_event(task)

    async def test_get_task_without_truncation_returns_full_content(self):
        """默认（面向 Agent）截断；`truncate_content=False`（Web）返回完整原文。"""
        task = await self._make_task(title="full-content")
        long_note = "打回原因：" + "很长的说明" * 30
        await transition_task(
            task, actor_id=8, from_status=TaskStatus.TODO,
            to_status=TaskStatus.IN_PROGRESS, content=long_note,
        )

        agent_view = await taskService.get_task(team_id=1, task_id=task.id)
        assert agent_view["task"]["last_note"].endswith(EVENT_TRUNCATED_SUFFIX)

        web_view = await taskService.get_task(team_id=1, task_id=task.id, truncate_content=False)
        assert web_view["task"]["last_note"] == long_note
        contents = [e["content"] for e in web_view["task"]["events"]]
        assert long_note in contents

    async def test_list_task_events_without_truncation_returns_full_content(self):
        task = await self._make_task(title="full-content-list")
        long_note = "补充说明：" + "细节" * 80
        await transition_task(
            task, actor_id=8, from_status=TaskStatus.TODO,
            to_status=TaskStatus.IN_PROGRESS, content=long_note,
        )

        agent_view = await taskService.list_task_events(team_id=1, task_id=task.id)
        assert agent_view["events"][0]["content"].endswith(EVENT_TRUNCATED_SUFFIX)

        web_view = await taskService.list_task_events(team_id=1, task_id=task.id, truncate_content=False)
        assert web_view["events"][0]["content"] == long_note
