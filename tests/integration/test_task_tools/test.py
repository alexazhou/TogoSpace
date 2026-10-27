"""#67 集成：任务工具面（`update_task` 内联评论 / `get_task_detail` / `list_task_events` / 去 `result`·`block_reason`）。

真库 + 工具层端到端：
  - 省略 `new_status` ⇒ 评论事件（from/to 均 NULL）、状态不变、action=commented；
  - 给定 `new_status` ⇒ 流转、action=transitioned；
  - 未知参数（旧字段 `result`）经 `run_tool_call` 直接报错（无兼容别名）；
  - `get_task_detail` 正常返回（`get_task` 废弃别名已在 #71 删除）；
  - `list_tasks` / `list_task_events` 工具签名与返回。
"""
import json
import os
import sys

import pytest

import service.ormService as ormService
import service.taskService as taskService
from constants import (
    EVENT_CONTENT_MAX_CHARS,
    EVENT_TRUNCATED_SUFFIX,
    SpecialAgent,
    TaskPriority,
    TaskStatus,
)
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
from service.funcToolService import core as funcCore
from service.funcToolService import tools
from service.roomService.core import ToolCallContext
from tests.base import ServiceTestCase
from tests.integration.task_helpers import create_task_with_event, transition_task


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
        return await create_task_with_event(task)

    async def _make_task_for(
        self,
        assignee_id: int,
        *,
        status: TaskStatus = TaskStatus.TODO,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> GtAgentTask:
        """按指定 assignee / 状态 / 优先级建任务（用于隔离的排序与过滤用例）。"""
        task = GtAgentTask(
            team_id=1,
            title="tool task",
            assignee_id=assignee_id,
            creator_id=8,
            status=status,
            priority=priority,
        )
        return await create_task_with_event(task)

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

    async def test_get_task_detail_returns_task(self):
        task = await self._make_task()

        res = await tools.get_task_detail(task_id=task.id, _context=self._ctx())
        assert res["success"] is True
        assert res["task"]["id"] == task.id
        assert "events" in res["task"]

    async def test_get_task_detail_event_limit_returns_latest_and_reports_total(self):
        """event_limit 只返回最新 N 条；events_total 为事件总数（跨 limit）。"""
        task = await self._make_task()  # 创建事件 1 条
        for i in range(3):
            await tools.update_task(task_id=task.id, note=f"备注{i}", _context=self._ctx())
        # 事件总数 = 1（创建）+ 3（评论）= 4

        res = await tools.get_task_detail(task_id=task.id, event_limit=1, _context=self._ctx())

        assert res["success"] is True
        events = res["task"]["events"]
        assert len(events) == 1
        assert events[0]["content"] == "备注2"  # 最新一条在前
        assert res["task"]["events_total"] == 4

    async def test_get_task_detail_default_event_limit_is_three(self):
        """不传 event_limit 时默认取最近 3 条（工具层默认 3）。"""
        task = await self._make_task()  # 1 条
        for i in range(4):
            await tools.update_task(task_id=task.id, note=f"备注{i}", _context=self._ctx())
        # 共 5 条事件

        res = await tools.get_task_detail(task_id=task.id, _context=self._ctx())

        assert len(res["task"]["events"]) == 3
        assert res["task"]["events_total"] == 5

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

    async def test_get_task_event_returns_full_single_event_with_actor_name(self):
        """按事件 id 取单条事件：字段完整、状态转字符串、actor_name 为展示名。

        内容超过 EVENT_CONTENT_MAX_CHARS 时仍返回**完整原文**（本工具不截断）。
        """
        task = await self._make_task()
        long_note = "打回原因：" + "很长的说明" * 30  # 远超 100 字符
        await transition_task(
            task,
            actor_id=SpecialAgent.SYSTEM.value,
            from_status=TaskStatus.TODO,
            to_status=TaskStatus.IN_PROGRESS,
            content=long_note,
        )
        latest = (await gtAgentTaskEventManager.list_events(task.id, limit=1))[0]

        res = await tools.get_task_event(event_id=latest.id, _context=self._ctx())

        assert res["success"] is True
        event = res["event"]
        assert event["id"] == latest.id
        assert event["task_id"] == task.id
        assert event["actor_id"] == SpecialAgent.SYSTEM.value
        assert event["from_status"] == "TODO"
        assert event["to_status"] == "IN_PROGRESS"
        assert event["content"] == long_note  # 完整原文，不截断
        assert event["actor_name"] == "系统"

    async def test_get_task_event_comment_event_has_null_statuses(self):
        """评论事件（from/to 均 NULL）取回后 actor_name 走 agent 展示名分支。"""
        task = await self._make_task()
        await tools.update_task(task_id=task.id, note="纯评论", _context=self._ctx())
        latest = (await gtAgentTaskEventManager.list_events(task.id, limit=1))[0]

        res = await tools.get_task_event(event_id=latest.id, _context=self._ctx())

        assert res["success"] is True
        assert res["event"]["from_status"] is None
        assert res["event"]["to_status"] is None
        assert res["event"]["content"] == "纯评论"
        # actor_id=8 在测试库中通常无对应 Agent ⇒ 回退占位名，但字段必须存在且非空
        assert isinstance(res["event"]["actor_name"], str) and res["event"]["actor_name"]

    async def test_get_task_event_not_found_returns_failure(self):
        """event_id 不存在 ⇒ 失败（success=False + message），不抛异常。"""
        res = await tools.get_task_event(event_id=10 ** 9, _context=self._ctx())

        assert res["success"] is False
        assert "事件不存在" in res["message"]
        assert "event" not in res

    async def test_get_task_event_cross_team_is_rejected(self):
        """跨团队事件 ⇒ 拒绝，且不泄露事件内容。"""
        other = GtAgentTask(
            team_id=2,
            title="other team task",
            assignee_id=8,
            creator_id=8,
            status=TaskStatus.TODO,
        )
        other = await create_task_with_event(other)
        latest = (await gtAgentTaskEventManager.list_events(other.id, limit=1))[0]

        res = await tools.get_task_event(event_id=latest.id, _context=self._ctx())  # 上下文 team_id=1

        assert res["success"] is False
        assert "事件不属于当前团队" in res["message"]
        assert "event" not in res

    async def test_list_tasks_tool_named_params(self):
        """工具层 list_tasks 用 assignee/reviewer 参数。"""
        task = await self._make_task()

        res = await tools.list_tasks(assignee=8, _context=self._ctx())

        assert res["success"] is True
        assert any(t["id"] == task.id for t in res["tasks"])

    async def test_list_tasks_default_open_only_excludes_finished(self):
        """默认 open_only=True：只返回未完结任务（排除 DONE / CANCELLED）。"""
        assignee = 71  # 独立 assignee，避免与其他用例的任务串扰
        open_task = await self._make_task_for(assignee, status=TaskStatus.IN_PROGRESS)
        done_task = await self._make_task_for(assignee, status=TaskStatus.DONE)
        cancelled_task = await self._make_task_for(assignee, status=TaskStatus.CANCELLED)

        res = await tools.list_tasks(assignee=assignee, _context=self._ctx())

        ids = [t["id"] for t in res["tasks"]]
        assert open_task.id in ids
        assert done_task.id not in ids
        assert cancelled_task.id not in ids

    async def test_list_tasks_status_done_returns_finished(self):
        """显式 status="DONE" 时能查到已完结任务（open_only 不生效）。"""
        assignee = 72
        await self._make_task_for(assignee, status=TaskStatus.IN_PROGRESS)
        done_task = await self._make_task_for(assignee, status=TaskStatus.DONE)

        res = await tools.list_tasks(assignee=assignee, status="DONE", _context=self._ctx())

        ids = [t["id"] for t in res["tasks"]]
        assert done_task.id in ids
        assert all(t["status"] == TaskStatus.DONE for t in res["tasks"])

    async def test_list_tasks_open_only_false_returns_finished(self):
        """open_only=False 时返回全部（含已完结）。"""
        assignee = 73
        open_task = await self._make_task_for(assignee, status=TaskStatus.TODO)
        done_task = await self._make_task_for(assignee, status=TaskStatus.DONE)

        res = await tools.list_tasks(assignee=assignee, open_only=False, _context=self._ctx())

        ids = [t["id"] for t in res["tasks"]]
        assert open_task.id in ids
        assert done_task.id in ids

    async def test_list_tasks_requires_assignee_or_reviewer(self):
        """assignee / reviewer 都不传 ⇒ missing_target 参数错误。"""
        res = await tools.list_tasks(_context=self._ctx())

        assert res["success"] is False
        assert res["error_code"] == "missing_target"

    async def test_list_tasks_sorted_latest_first(self):
        """工具层排序契约（#83 改动）：**id 倒序 = 最新在前**，与优先级无关。

        为什么工具层要倒序：Agent 关注「刚发生的事」；Web 列表接口仍走
        `taskService.list_tasks` 的默认优先级排序，两路互不影响（见下一个用例）。
        构造 id 与优先级交错的序列，使「按优先级排」的实现必然排错。
        """
        assignee = 74
        high1 = await self._make_task_for(assignee, priority=TaskPriority.HIGH)
        normal = await self._make_task_for(assignee, priority=TaskPriority.NORMAL)
        low = await self._make_task_for(assignee, priority=TaskPriority.LOW)
        high2 = await self._make_task_for(assignee, priority=TaskPriority.HIGH)

        res = await tools.list_tasks(assignee=assignee, _context=self._ctx())

        assert res["success"] is True
        # id 倒序：最后创建的排最前（high2 > low > normal > high1）
        assert [t["id"] for t in res["tasks"]] == [high2.id, low.id, normal.id, high1.id]
        # 顺手钉住「这不是优先级排序」：优先级序列不应是 HIGH→HIGH→NORMAL→LOW
        assert [t["priority"] for t in res["tasks"]] == [
            TaskPriority.HIGH,
            TaskPriority.LOW,
            TaskPriority.NORMAL,
            TaskPriority.HIGH,
        ]

    async def test_service_list_tasks_keeps_priority_order_for_web(self):
        """**Web 路径默认排序零变化**：`taskService.list_tasks` 不传 order ⇒ 优先级优先。

        `agentController.py` 的两处列表接口走的就是这条链路；改默认值会静默翻掉前端顺序。
        """
        assignee = 80  # 独立 assignee，避免与相邻用例串扰
        high1 = await self._make_task_for(assignee, priority=TaskPriority.HIGH)
        normal = await self._make_task_for(assignee, priority=TaskPriority.NORMAL)
        low = await self._make_task_for(assignee, priority=TaskPriority.LOW)
        high2 = await self._make_task_for(assignee, priority=TaskPriority.HIGH)

        res = await taskService.list_tasks(team_id=1, assignee_id=assignee)

        assert [t["id"] for t in res["tasks"]] == [high1.id, high2.id, normal.id, low.id]

    async def test_dal_list_tasks_sorted_by_priority_then_id(self):
        """DAL 层直接校验排序（不经过工具层）。"""
        assignee = 75
        high = await self._make_task_for(assignee, priority=TaskPriority.HIGH)
        normal = await self._make_task_for(assignee, priority=TaskPriority.NORMAL)
        low = await self._make_task_for(assignee, priority=TaskPriority.LOW)

        rows = await gtAgentTaskManager.list_tasks(team_id=1, assignee_id=assignee)

        assert [t.id for t in rows] == [high.id, normal.id, low.id]

    async def test_list_tasks_total_is_real_total_not_page_len(self):
        """#67：`total` = 真实总数（跨页），`count` = 本页条数（被默认 limit=20 截断）。

        建 21 条未完结任务 ⇒ 不传 limit 时默认 20，故 count=20、total=21，
        Agent 可据此察觉「还有更多任务」；旧实现 total=len(tasks)=20，本用例在其下必红。
        """
        assignee = 76
        for _ in range(21):
            await self._make_task_for(assignee, status=TaskStatus.TODO)

        res = await tools.list_tasks(assignee=assignee, _context=self._ctx())

        assert res["success"] is True
        assert res["count"] == 20
        assert res["total"] == 21
        assert len(res["tasks"]) == 20

    async def test_list_tasks_total_respects_open_only_filter(self):
        """总数与列表同一过滤口径：open_only 默认排除 DONE/CANCELLED，`total` 也不含它们。"""
        assignee = 78
        await self._make_task_for(assignee, status=TaskStatus.TODO)
        await self._make_task_for(assignee, status=TaskStatus.IN_PROGRESS)
        await self._make_task_for(assignee, status=TaskStatus.DONE)

        open_res = await tools.list_tasks(assignee=assignee, _context=self._ctx())
        all_res = await tools.list_tasks(assignee=assignee, open_only=False, _context=self._ctx())

        assert (open_res["count"], open_res["total"]) == (2, 2)
        assert (all_res["count"], all_res["total"]) == (3, 3)

    async def test_dal_count_tasks_shares_filters_with_list_tasks(self):
        """DAL：`count_tasks` 与 `list_tasks` 共用同一 where 构造 ⇒ 同参数下总数==列表条数。"""
        assignee = 79
        await self._make_task_for(assignee, status=TaskStatus.TODO)
        await self._make_task_for(assignee, status=TaskStatus.IN_PROGRESS)
        await self._make_task_for(assignee, status=TaskStatus.DONE)
        closed = [TaskStatus.DONE, TaskStatus.CANCELLED]

        rows = await gtAgentTaskManager.list_tasks(
            team_id=1, assignee_id=assignee, exclude_statuses=closed, limit=20
        )
        count = await gtAgentTaskManager.count_tasks(
            team_id=1, assignee_id=assignee, exclude_statuses=closed
        )
        count_all = await gtAgentTaskManager.count_tasks(team_id=1, assignee_id=assignee)

        assert count == len(rows) == 2  # 同一过滤口径
        assert count_all == 3  # 不带过滤时含已完结

    async def test_event_content_truncation_detail_vs_full_event(self):
        """#65：同一条 >100 字事件，get_task_detail 截断、get_task_event 返回完整原文（互为对照）。

        旧实现（get_task_detail 回传完整 content）在本用例下必红；
        本用例同时守卫 #66（get_task_event 是唯一不截断的原文入口）。
        """
        task = await self._make_task()
        long_note = "打回原因：" + "很长的说明" * 30
        assert len(long_note) > EVENT_CONTENT_MAX_CHARS
        await tools.update_task(task_id=task.id, note=long_note, _context=self._ctx())
        latest = (await gtAgentTaskEventManager.list_events(task.id, limit=1))[0]

        detail = await tools.get_task_detail(task_id=task.id, _context=self._ctx())
        single = await tools.get_task_event(event_id=latest.id, _context=self._ctx())

        expected = long_note[:EVENT_CONTENT_MAX_CHARS] + EVENT_TRUNCATED_SUFFIX
        detail_content = detail["task"]["events"][0]["content"]
        assert detail_content == expected
        assert detail_content != long_note
        # 同一条事件的完整原文必须由 get_task_event 取回
        assert single["event"]["id"] == latest.id
        assert single["event"]["content"] == long_note

    async def test_list_task_events_truncates_long_content(self):
        """#65：list_task_events 与 get_task_detail 共用同一截断口径。"""
        task = await self._make_task()
        long_note = "很长的说明" * 30
        await tools.update_task(task_id=task.id, note=long_note, _context=self._ctx())

        res = await tools.list_task_events(task_id=task.id, _context=self._ctx())

        expected = long_note[:EVENT_CONTENT_MAX_CHARS] + EVENT_TRUNCATED_SUFFIX
        assert res["events"][0]["content"] == expected

    async def test_get_task_detail_truncates_last_note_and_block_reason(self):
        """#65：派生字段 last_note / current_block_reason 同样按 100 字符截断。"""
        task = await self._make_task()
        long_note = "等待外部依赖：" + "很长的说明" * 30
        res_t = await tools.update_task(
            task_id=task.id, new_status="ON_HOLD", note=long_note, _context=self._ctx()
        )
        assert res_t["success"] is True

        res = await tools.get_task_detail(task_id=task.id, _context=self._ctx())

        expected = long_note[:EVENT_CONTENT_MAX_CHARS] + EVENT_TRUNCATED_SUFFIX
        assert res["task"]["last_note"] == expected
        assert res["task"]["current_block_reason"] == expected


class TestListTasksNameKeywordRealDb(ServiceTestCase):
    """C-5：`name_keyword` 的 **真实库 + 真实 `GtAgent.display_name`** 链路（此前零覆盖）。

    为什么单列一类：原有 6 条 `name_keyword` 用例全在
    `tests/unit/service/test_task_service.py`，Agent 一律用 `SimpleNamespace`
    替身喂给 `gtAgentManager.get_team_all_agents` 的 mock。那套用例能证明
    「比对逻辑用了 display_name 字段」，但**证明不了**下面这条真实链路：

        GtAgent 行（i18n JSON 落库）→ `display_name` property → `i18nUtil.extract_i18n_str`
        → `get_team_all_agents` 真查询 → 关键字比对 → `agent_ids` 进 SQL

    这条链上任何一环坏掉（i18n 字段名写错、property 回退到 `name`、查询漏 team 过滤、
    `in_([])` 被写成「不过滤」），替身用例都发现不了 —— 因为替身的 `display_name`
    是**直接给死的字符串**。所以必须真库跑一遍。
    """

    TEAM = "name_keyword_real_team"

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

        team = await gtTeamManager.save_team(GtTeam(name=cls.TEAM))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="name_keyword_real_role", soul="soul")
        )
        cls.role_template_id = template.id

        await gtAgentManager.batch_save_agents(
            team.id,
            [
                # 中文 display_name —— 「搜中文名」是这条用例的主目标
                GtAgent(
                    team_id=team.id,
                    name="gateway_dev",
                    role_template_id=template.id,
                    i18n={"display_name": {"zh-CN": "网关开发", "en": "Gateway Dev"}},
                ),
                GtAgent(
                    team_id=team.id,
                    name="togo_dev",
                    role_template_id=template.id,
                    i18n={"display_name": {"zh-CN": "阿张", "en": "Zhang"}},
                ),
                # 没有 i18n 的成员：display_name 必须回退成 name，否则搜英文标识符会漏
                GtAgent(team_id=team.id, name="plain_bot", role_template_id=template.id),
            ],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.agent_ids = {a.name: a.id for a in agents}

        # 两个任务：分别挂在两个中文名成员名下，用来验证「搜谁只出谁」
        cls.task_gateway = await create_task_with_event(
            GtAgentTask(
                team_id=team.id,
                title="网关侧任务",
                assignee_id=cls.agent_ids["gateway_dev"],
                creator_id=cls.agent_ids["gateway_dev"],
                status=TaskStatus.TODO,
            )
        )
        cls.task_togo = await create_task_with_event(
            GtAgentTask(
                team_id=team.id,
                title="Togo 侧任务",
                assignee_id=cls.agent_ids["togo_dev"],
                creator_id=cls.agent_ids["togo_dev"],
                status=TaskStatus.TODO,
            )
        )

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def _ctx(self, *, tool_name: str = "list_tasks") -> ToolCallContext:
        return ToolCallContext(
            agent_id=self.agent_ids["gateway_dev"], team_id=self.team_id, tool_name=tool_name
        )

    async def test_display_name_property_reads_i18n_from_real_row(self):
        """前提校验：真库读回的 `display_name` 必须是 i18n 中文名，而不是回退成 `name`。

        没有这条，下面的用例可能「因为两边都回退到 name 而碰巧通过」——那就成了假绿。
        """
        agents = await gtAgentManager.get_team_all_agents(self.team_id)
        by_name = {a.name: a for a in agents}

        assert by_name["gateway_dev"].display_name == "网关开发"
        assert by_name["togo_dev"].display_name == "阿张"
        # 无 i18n 的行必须回退到 name（这是 display_name 的官方口径）
        assert by_name["plain_bot"].display_name == "plain_bot"

    async def test_name_keyword_matches_chinese_display_name(self):
        """搜中文 display_name「网关」⇒ 只命中 gateway_dev 的任务。

        `display_name` 是 `GtAgent` 上的 Python property，**SQL 层查不到**；
        实现若图省事写成 `name LIKE '%网关%'`，真实库里一条都匹配不到 —— 本用例必红。
        """
        res = await tools.list_tasks(name_keyword="网关", _context=self._ctx())

        assert res["success"] is True
        ids = [t["id"] for t in res["tasks"]]
        assert self.task_gateway.id in ids
        assert self.task_togo.id not in ids
        # 返回体里的执行人名也要能解析成中文（同一个 i18n 链路的另一处消费点）
        assert any(t["assignee_name"] == "网关开发" for t in res["tasks"])

    async def test_name_keyword_no_match_returns_empty_not_whole_team(self):
        """搜不存在的人名 ⇒ **空列表**，绝不退化成「不过滤」。

        退化实现（`agent_ids=None`）下这里会拿到团队全部任务，模型会以为自己搜到了结果。
        这是「不能静默降级」在真实库上的验证：空集合走 `in_([])` 天然查不到东西，
        而不是把过滤条件整个丢掉。
        """
        res = await tools.list_tasks(name_keyword="查无此人确实不存在", _context=self._ctx())

        assert res["success"] is True
        assert res["tasks"] == []
        assert res["count"] == 0
        assert res["total"] == 0

    async def test_name_keyword_matches_english_display_name_case_insensitively(self):
        """英文 display_name 也要命中，且**大小写不敏感**（真库 + 真 property）。

        `GATEWAY` → `Gateway Dev`：去掉 `keyword.lower()` 归一后本用例必红。
        """
        res_en = await tools.list_tasks(name_keyword="GATEWAY", _context=self._ctx())

        assert [t["id"] for t in res_en["tasks"]] == [self.task_gateway.id]

    async def test_name_keyword_matches_agent_without_i18n_by_name_fallback(self):
        """无 i18n 的成员靠 `display_name` 回退到 `name` 才能被搜到。

        `plain_bot` 名下没有任务，所以这里直接查解析结果（`_resolve_agent_ids_by_name_keyword`）
        而不是走 `tools.list_tasks` —— 否则「查不到任务」会被误读成「匹配不到人」，
        用例就失去了区分力。
        """
        keyword_ids = await taskService._resolve_agent_ids_by_name_keyword(self.team_id, "PLAIN")

        assert keyword_ids == [self.agent_ids["plain_bot"]]

    async def test_name_keyword_does_not_leak_across_teams(self):
        """关键字匹配只在**本团队**范围内 —— 别的团队同名成员不能被搜出来。

        实现若漏了 `team_id` 过滤（比如改成全表扫描），这里会把外团队的 Agent 也带进来。
        """
        other = await gtTeamManager.save_team(GtTeam(name="name_keyword_other_team"))
        await gtAgentManager.batch_save_agents(
            other.id,
            [
                GtAgent(
                    team_id=other.id,
                    name="gateway_dev_other",
                    role_template_id=self.role_template_id,
                    i18n={"display_name": {"zh-CN": "网关开发二分部"}},
                )
            ],
        )

        keyword_ids = await taskService._resolve_agent_ids_by_name_keyword(self.team_id, "网关")

        assert keyword_ids == [self.agent_ids["gateway_dev"]]

