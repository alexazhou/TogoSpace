"""test_task_service 单元测试：测试任务服务。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import EVENT_CONTENT_MAX_CHARS, EVENT_TRUNCATED_SUFFIX, SpecialAgent, TaskPriority, TaskStatus
from model.dbModel.gtAgentTask import GtAgentTask
from service import taskService


@pytest.fixture
def task_manager_mock():
    """统一 mock 任务 DAL，避免访问真实数据库。"""
    with patch("service.taskService.gtAgentTaskManager") as mock_manager:
        mock_manager.list_pending_dependents = AsyncMock(return_value=[])
        yield mock_manager


@pytest.fixture(autouse=True)
def event_manager_mock():
    """统一 mock 事件 DAL，避免 get_task / list_task_events 访问真实数据库。"""
    with patch("service.taskService.gtAgentTaskEventManager") as mock_events:
        mock_events.list_events = AsyncMock(return_value=[])
        mock_events.count_events = AsyncMock(return_value=0)
        yield mock_events


@pytest.fixture(autouse=True)
def mock_model_to_dict():
    """简化 peewee model_to_dict，便于对 MagicMock 断言。"""

    def _fake_model_to_dict(task, recurse=False):
        status = getattr(task, "status", None)
        priority = getattr(task, "priority", None)
        return {
            "id": getattr(task, "id", None),
            "team_id": getattr(task, "team_id", None),
            "title": getattr(task, "title", None),
            "description": getattr(task, "description", ""),
            "assignee_id": getattr(task, "assignee_id", None),
            "creator_id": getattr(task, "creator_id", None),
            "reviewer_id": getattr(task, "reviewer_id", None),
            "status": status.value if isinstance(status, TaskStatus) else status,
            "priority": priority.value if isinstance(priority, TaskPriority) else priority,
            "depends_on": list(getattr(task, "depends_on", []) or []),
        }

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_fake_model_to_dict):
        yield


@pytest.fixture
def saved_task():
    task = MagicMock(spec=GtAgentTask)
    task.id = 42
    return task


def _build_task(**overrides):
    """构造任务 mock，减少重复样板代码。"""
    task = MagicMock(spec=GtAgentTask)
    task.id = overrides.get("id", 1)
    task.team_id = overrides.get("team_id", 1)
    task.title = overrides.get("title", "test task")
    task.description = overrides.get("description", "")
    task.assignee_id = overrides.get("assignee_id", 11)
    task.creator_id = overrides.get("creator_id", 11)
    task.reviewer_id = overrides.get("reviewer_id")
    task.status = overrides.get("status", TaskStatus.TODO)
    task.priority = overrides.get("priority", TaskPriority.NORMAL)
    task.depends_on = overrides.get("depends_on", [])
    return task


@pytest.mark.asyncio
async def test_create_task_same_assignee_and_reviewer_returns_failure(task_manager_mock):
    """assignee 与 reviewer 相同时，应拒绝创建任务。"""
    result = await taskService.create_task(
        team_id=1,
        creator_id=11,
        title="self review",
        assignee_id=11,
        reviewer_id=11,
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_reviewer"
    assert "reviewer" in result["message"]
    task_manager_mock.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_task_success_self_assign(task_manager_mock, saved_task):
    """创建人给自己派单时应成功创建任务。"""
    task_manager_mock.create_task = AsyncMock(return_value=saved_task)

    result = await taskService.create_task(
        team_id=1,
        creator_id=11,
        title="self task",
        assignee_id=11,
        description="desc",
    )

    assert result == {"success": True, "task_id": 42, "message": "任务已创建，task_id=42，状态=TODO"}
    task_manager_mock.create_task.assert_awaited_once()
    created_task = task_manager_mock.create_task.await_args.args[0]
    assert isinstance(created_task, GtAgentTask)
    assert created_task.team_id == 1
    assert created_task.creator_id == 11
    assert created_task.assignee_id == 11
    assert created_task.title == "self task"
    assert created_task.description == "desc"
    assert created_task.status == TaskStatus.TODO
    assert created_task.priority == TaskPriority.NORMAL


@pytest.mark.asyncio
async def test_create_task_success_manager_assigns_subordinate(task_manager_mock, saved_task):
    """部门主管可以给下属派发任务。"""
    task_manager_mock.create_task = AsyncMock(return_value=saved_task)

    with patch("service.deptService.get_sub_agent_ids", new=AsyncMock(return_value={21, 22})):
        result = await taskService.create_task(
            team_id=1,
            creator_id=20,
            title="assign task",
            assignee_id=21,
            reviewer_id=20,
        )

    assert result["success"] is True
    assert result["task_id"] == 42
    created_task = task_manager_mock.create_task.await_args.args[0]
    assert created_task.assignee_id == 21
    assert created_task.reviewer_id == 20


@pytest.mark.asyncio
async def test_create_task_permission_denied_when_assigning_non_subordinate(task_manager_mock):
    """非主管给他人派单时应被拒绝。"""
    with patch("service.deptService.get_sub_agent_ids", new=AsyncMock(return_value={31})):
        result = await taskService.create_task(
            team_id=1,
            creator_id=20,
            title="assign task",
            assignee_id=99,
        )

    assert result["success"] is False
    assert result["error_code"] == "assignee_not_allowed"
    task_manager_mock.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_task_invalid_priority_returns_failure(task_manager_mock):
    result = await taskService.create_task(
        team_id=1,
        creator_id=11,
        title="bad priority",
        assignee_id=11,
        priority="urgent",
    )

    assert result["success"] is False
    assert "无效的优先级" in result["message"]
    task_manager_mock.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_task_depends_on_non_existent_tasks_returns_failure(task_manager_mock):
    """依赖任务缺失时不应创建。"""
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=[])

    result = await taskService.create_task(
        team_id=1,
        creator_id=11,
        title="dep task",
        assignee_id=11,
        depends_on=[100, 101],
    )

    assert result["success"] is False
    assert "依赖任务不存在" in result["message"]
    task_manager_mock.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_task_with_valid_depends_on_sets_pending_status(task_manager_mock, saved_task):
    """依赖存在但未完成时，初始状态应为 PENDING。"""
    dep_task = _build_task(id=7, team_id=1, status=TaskStatus.IN_PROGRESS)
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=[dep_task])
    task_manager_mock.create_task = AsyncMock(return_value=saved_task)

    result = await taskService.create_task(
        team_id=1,
        creator_id=11,
        title="dep task",
        assignee_id=11,
        depends_on=[7],
    )

    assert result["success"] is True
    created_task = task_manager_mock.create_task.await_args.args[0]
    assert created_task.depends_on == [7]
    assert created_task.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_update_task_assignee_submits_for_review(task_manager_mock):
    """执行人可将进行中的任务提交为 REVIEWING。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="REVIEWING")

    assert result["success"] is True
    assert result["task"]["status"] == "REVIEWING"
    task_manager_mock.update_task_status_with_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_task_manager_approves_review(task_manager_mock):
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=20, task_id=1, new_status="DONE")

    assert result["success"] is True
    assert result["task"]["status"] == "DONE"


@pytest.mark.asyncio
async def test_update_task_manager_rejects_review(task_manager_mock):
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=20, task_id=1, new_status="IN_PROGRESS")

    assert result["success"] is True
    assert result["task"]["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_update_task_non_assignee_cannot_update(task_manager_mock):
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=12, task_id=1, new_status="IN_PROGRESS")

    assert result["success"] is False
    assert result["error_code"] == "permission_denied"


@pytest.mark.asyncio
async def test_update_task_invalid_state_transition_returns_failure(task_manager_mock):
    task = _build_task(status=TaskStatus.DONE, assignee_id=11, creator_id=11)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="TODO")

    assert result["success"] is False
    assert result["error_code"] == "invalid_transition"


@pytest.mark.asyncio
async def test_update_task_pending_to_in_progress_blocked_by_unfinished_dependency(task_manager_mock):
    task = _build_task(status=TaskStatus.PENDING, assignee_id=11, creator_id=11, depends_on=[7])
    dep_task = _build_task(id=7, status=TaskStatus.IN_PROGRESS)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=[dep_task])

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="IN_PROGRESS")

    assert result["success"] is False
    assert result["error_code"] == "dependency_not_met"


@pytest.mark.asyncio
async def test_update_task_pending_to_in_progress_allowed_when_dependency_done(task_manager_mock):
    task = _build_task(status=TaskStatus.PENDING, assignee_id=11, creator_id=11, depends_on=[7])
    dep_task = _build_task(id=7, status=TaskStatus.DONE)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=[dep_task])
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="IN_PROGRESS")

    assert result["success"] is True
    assert result["task"]["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_update_task_invalid_status_string_returns_failure(task_manager_mock):
    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="not-a-status")

    assert result["success"] is False
    assert "无效的状态" in result["message"]
    task_manager_mock.get_task.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_not_found_returns_failure(task_manager_mock):
    task_manager_mock.get_task = AsyncMock(return_value=None)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=999, new_status="IN_PROGRESS")

    assert result["success"] is False
    assert "任务不存在" in result["message"]


@pytest.mark.asyncio
async def test_update_task_different_team_returns_failure(task_manager_mock):
    task = _build_task(team_id=2, assignee_id=11, creator_id=11)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="IN_PROGRESS")

    assert result["success"] is False
    assert "任务不属于当前团队" in result["message"]


@pytest.mark.asyncio
async def test_update_task_assignee_cannot_cancel(task_manager_mock):
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=30, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="CANCELLED")

    assert result["success"] is False
    assert result["error_code"] == "permission_denied"


@pytest.mark.asyncio
async def test_update_task_manager_can_cancel(task_manager_mock):
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=30, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=20, task_id=1, new_status="CANCELLED")

    assert result["success"] is True
    assert result["task"]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_get_task_success_returns_task_dict(task_manager_mock):
    task = _build_task(id=1, team_id=1, title="main task", depends_on=[7])
    dep_task = _build_task(id=7, title="dep task", status=TaskStatus.DONE)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=[dep_task])

    result = await taskService.get_task(team_id=1, task_id=1)

    assert result["success"] is True
    assert result["task"]["id"] == 1
    assert result["task"]["depends_on_details"] == [{"task_id": 7, "title": "dep task", "status": "DONE"}]


@pytest.mark.asyncio
async def test_get_task_not_found_returns_failure(task_manager_mock):
    task_manager_mock.get_task = AsyncMock(return_value=None)

    result = await taskService.get_task(team_id=1, task_id=1)

    assert result["success"] is False
    assert "任务不存在" in result["message"]


@pytest.mark.asyncio
async def test_get_task_wrong_team_returns_failure(task_manager_mock):
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(team_id=2))

    result = await taskService.get_task(team_id=1, task_id=1)

    assert result["success"] is False
    assert "任务不属于当前团队" in result["message"]


@pytest.mark.asyncio
async def test_list_tasks_basic_call_returns_tasks(task_manager_mock):
    tasks = [
        _build_task(id=1, title="task 1", status=TaskStatus.TODO),
        _build_task(id=2, title="task 2", status=TaskStatus.DONE),
    ]
    task_manager_mock.list_tasks = AsyncMock(return_value=tasks)
    task_manager_mock.count_tasks = AsyncMock(return_value=2)

    result = await taskService.list_tasks(team_id=1)

    assert result["success"] is True
    assert result["count"] == 2  # 本页条数
    assert result["total"] == 2  # 符合条件的总数
    assert [task["id"] for task in result["tasks"]] == [1, 2]
    task_manager_mock.list_tasks.assert_awaited_once_with(
        team_id=1,
        assignee_id=None,
        reviewer_id=None,
        status=None,
        exclude_statuses=None,
        limit=20,
    )
    task_manager_mock.count_tasks.assert_awaited_once_with(
        team_id=1,
        assignee_id=None,
        reviewer_id=None,
        status=None,
        exclude_statuses=None,
    )


@pytest.mark.asyncio
async def test_list_tasks_total_is_real_total_not_page_len(task_manager_mock):
    """#67 回归：`total` = **真实总数**（跨页），`count` = 本页条数。

    旧实现把 `total` 写成 `len(tasks)` ⇒ 本用例在其下必红（total 会是 20）。
    """
    page = [_build_task(id=i) for i in range(1, 21)]  # 本页满 20 条
    task_manager_mock.list_tasks = AsyncMock(return_value=page)
    task_manager_mock.count_tasks = AsyncMock(return_value=37)  # 实际共 37 条

    result = await taskService.list_tasks(team_id=1, assignee_id=11, limit=20)

    assert result["count"] == 20
    assert result["total"] == 37  # 被 limit 截断可被察觉（total > count）
    task_manager_mock.count_tasks.assert_awaited_once_with(
        team_id=1,
        assignee_id=11,
        reviewer_id=None,
        status=None,
        exclude_statuses=None,
    )


@pytest.mark.asyncio
async def test_list_tasks_counts_with_same_filters(task_manager_mock):
    """总数与列表必须走**同一过滤口径**（status / exclude_statuses 一并透传）。"""
    task_manager_mock.list_tasks = AsyncMock(return_value=[])
    task_manager_mock.count_tasks = AsyncMock(return_value=0)

    await taskService.list_tasks(team_id=1, assignee_id=11, open_only=True, limit=10)

    expected = dict(
        team_id=1,
        assignee_id=11,
        reviewer_id=None,
        status=None,
        exclude_statuses=[TaskStatus.DONE, TaskStatus.CANCELLED],
    )
    task_manager_mock.list_tasks.assert_awaited_once_with(limit=10, **expected)
    task_manager_mock.count_tasks.assert_awaited_once_with(**expected)


@pytest.mark.asyncio
async def test_list_tasks_explicit_status_is_passed_to_both_queries(task_manager_mock):
    """显式 status 时 `open_only` 不生效，且过滤条件同样透传给总数查询。"""
    task_manager_mock.list_tasks = AsyncMock(return_value=[])
    task_manager_mock.count_tasks = AsyncMock(return_value=0)

    await taskService.list_tasks(team_id=1, assignee_id=11, status="done", open_only=True)

    expected = dict(
        team_id=1,
        assignee_id=11,
        reviewer_id=None,
        status=TaskStatus.DONE,
        exclude_statuses=None,
    )
    task_manager_mock.list_tasks.assert_awaited_once_with(limit=20, **expected)
    task_manager_mock.count_tasks.assert_awaited_once_with(**expected)


@pytest.mark.asyncio
async def test_create_task_publishes_task_created_event(task_manager_mock, saved_task):
    """创建任务成功时应发布 TASK_CREATED 事件。"""
    task_manager_mock.create_task = AsyncMock(return_value=saved_task)

    with patch("service.messageBus.publish") as mock_publish:
        result = await taskService.create_task(
            team_id=1,
            creator_id=11,
            title="broadcast test",
            assignee_id=11,
        )

    assert result["success"] is True
    mock_publish.assert_called_once()
    topic = mock_publish.call_args.args[0]
    assert topic.name == "TASK_CREATED"
    assert mock_publish.call_args.kwargs["task"] is saved_task


@pytest.mark.asyncio
async def test_update_task_publishes_task_changed_event(task_manager_mock):
    """更新任务状态成功时应发布 TASK_CHANGED 事件并携带 old_status。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    with patch("service.messageBus.publish") as mock_publish:
        result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="REVIEWING")

    assert result["success"] is True
    mock_publish.assert_called_once()
    topic = mock_publish.call_args.args[0]
    assert topic.name == "TASK_CHANGED"
    assert mock_publish.call_args.kwargs["task"] is task
    assert mock_publish.call_args.kwargs["old_status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_create_task_does_not_publish_on_failure(task_manager_mock):
    """创建任务失败时不应发布任何事件。"""
    with patch("service.messageBus.publish") as mock_publish:
        result = await taskService.create_task(
            team_id=1,
            creator_id=11,
            title="bad priority",
            assignee_id=11,
            priority="urgent",
        )

    assert result["success"] is False
    mock_publish.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_does_not_publish_on_failure(task_manager_mock):
    """更新任务失败时不应发布任何事件。"""
    task_manager_mock.get_task = AsyncMock(return_value=None)

    with patch("service.messageBus.publish") as mock_publish:
        result = await taskService.update_task(team_id=1, caller_id=11, task_id=999, new_status="IN_PROGRESS")

    assert result["success"] is False
    mock_publish.assert_not_called()


@pytest.mark.asyncio
async def test_list_tasks_open_only_excludes_done_and_cancelled(task_manager_mock):
    task_manager_mock.list_tasks = AsyncMock(return_value=[_build_task(id=3, status=TaskStatus.IN_PROGRESS)])
    task_manager_mock.count_tasks = AsyncMock(return_value=1)

    result = await taskService.list_tasks(team_id=1, assignee_id=11, open_only=True, limit=10)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["total"] == 1
    task_manager_mock.list_tasks.assert_awaited_once_with(
        team_id=1,
        assignee_id=11,
        reviewer_id=None,
        status=None,
        exclude_statuses=[TaskStatus.DONE, TaskStatus.CANCELLED],
        limit=10,
    )


# ---------------------------------------------------------------------------
# #64 C-1：新增行为分支 / 新边的测试覆盖（阿张复核要求）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_no_reviewer_rejects_reviewing(task_manager_mock):
    """未设验收人时，IN_PROGRESS → REVIEWING 必须被拒绝（no_reviewer）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="REVIEWING")

    assert result["success"] is False
    assert result["error_code"] == "no_reviewer"
    task_manager_mock.update_task_status_with_event.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_review_required_blocks_direct_done_from_in_progress(task_manager_mock):
    """设验收人时，执行人不能从 IN_PROGRESS 直接 DONE（review_required）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="DONE")

    assert result["success"] is False
    assert result["error_code"] == "review_required"
    task_manager_mock.update_task_status_with_event.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_review_required_blocks_direct_done_from_todo(task_manager_mock):
    """设验收人时，执行人不能从 TODO 直接 DONE（review_required）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="DONE")

    assert result["success"] is False
    assert result["error_code"] == "review_required"


@pytest.mark.asyncio
async def test_update_task_pending_to_todo_allowed(task_manager_mock):
    """依赖已满足时，可将 PENDING 推进为 TODO（系统推进边合法）。"""
    task = _build_task(status=TaskStatus.PENDING, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="TODO")

    assert result["success"] is True
    assert result["task"]["status"] == "TODO"


@pytest.mark.asyncio
async def test_update_task_pending_to_on_hold_allowed(task_manager_mock):
    """PENDING → ON_HOLD 合法。"""
    task = _build_task(status=TaskStatus.PENDING, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="ON_HOLD")

    assert result["success"] is True
    assert result["task"]["status"] == "ON_HOLD"


@pytest.mark.asyncio
async def test_update_task_reviewing_to_cancelled_by_reviewer(task_manager_mock):
    """验收人可取消 REVIEWING 任务（REVIEWING → CANCELLED）。"""
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=30, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=20, task_id=1, new_status="CANCELLED")

    assert result["success"] is True
    assert result["task"]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_update_task_non_assignee_cannot_hold(task_manager_mock):
    """非执行人推进 ON_HOLD 必须被拒绝（permission_denied）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=12, task_id=1, new_status="ON_HOLD")

    assert result["success"] is False
    assert result["error_code"] == "permission_denied"


# ---------------------------------------------------------------------------
# #66：事件流读取服务 + 派生属性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_task_events_passes_cursor_and_returns_events(task_manager_mock, event_manager_mock):
    """list_task_events 应按 id 游标透传 limit/before_id，并返回事件列表。"""
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(
        return_value=[SimpleNamespace(id=9, task_id=1, actor_id=8)]
    )
    event_manager_mock.count_events = AsyncMock(return_value=3)

    result = await taskService.list_task_events(team_id=1, task_id=1, limit=10, before_id=5)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["total"] == 3
    event_manager_mock.list_events.assert_awaited_once_with(1, limit=10, before_id=5)
    event_manager_mock.count_events.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_list_task_events_total_is_cross_page_not_page_len(task_manager_mock, event_manager_mock):
    """F-a 回归：`total` = 该任务事件总数（跨页），不随 `limit` / `before_id` 缩小。

    旧实现把 `total` 写成当前页条数（`len(events)`）⇒ 本用例在其下必红（limit=1 时 total=1）。
    """
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(
        return_value=[SimpleNamespace(id=5, task_id=1, actor_id=8)]
    )
    event_manager_mock.count_events = AsyncMock(return_value=4)

    result = await taskService.list_task_events(team_id=1, task_id=1, limit=1)

    assert result["count"] == 1  # 本页条数（被 limit 截断）
    assert result["total"] == 4  # 跨页总数（不受 limit 影响）


@pytest.mark.asyncio
async def test_list_task_events_not_found(task_manager_mock):
    task_manager_mock.get_task = AsyncMock(return_value=None)

    result = await taskService.list_task_events(team_id=1, task_id=999)

    assert result["success"] is False
    assert "任务不存在" in result["message"]


@pytest.mark.asyncio
async def test_list_task_events_wrong_team(task_manager_mock):
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=2))

    result = await taskService.list_task_events(team_id=1, task_id=1)

    assert result["success"] is False
    assert "任务不属于当前团队" in result["message"]


@pytest.mark.asyncio
async def test_get_task_attaches_events_and_derived_props(task_manager_mock, event_manager_mock):
    """get_task 应挂载事件时间线摘要，并给出「最近一次说明」「当前搁置原因」。"""
    task = _build_task(id=1, team_id=1, status=TaskStatus.ON_HOLD)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(return_value=[
        SimpleNamespace(id=5, task_id=1, actor_id=8, from_status=TaskStatus.IN_PROGRESS, to_status=TaskStatus.ON_HOLD, content="等接口"),
        SimpleNamespace(id=1, task_id=1, actor_id=8, from_status=None, to_status=TaskStatus.TODO, content=""),
    ])

    result = await taskService.get_task(team_id=1, task_id=1)

    assert result["success"] is True
    assert len(result["task"]["events"]) == 2
    assert result["task"]["last_note"] == "等接口"
    assert result["task"]["current_block_reason"] == "等接口"


@pytest.mark.asyncio
async def test_get_task_derived_props_ignore_comment_and_non_hold(task_manager_mock, event_manager_mock):
    """评论（to_status=NULL）不计入「最近一次说明」；非 ON_HOLD 时「当前搁置原因」为 None。"""
    task = _build_task(id=1, team_id=1, status=TaskStatus.IN_PROGRESS)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(return_value=[
        SimpleNamespace(id=9, task_id=1, actor_id=8, from_status=None, to_status=None, content="纯评论"),
        SimpleNamespace(id=3, task_id=1, actor_id=8, from_status=TaskStatus.TODO, to_status=TaskStatus.IN_PROGRESS, content="开工"),
        SimpleNamespace(id=1, task_id=1, actor_id=8, from_status=None, to_status=TaskStatus.TODO, content=""),
    ])

    result = await taskService.get_task(team_id=1, task_id=1)

    assert result["task"]["last_note"] == "开工"
    assert result["task"]["current_block_reason"] is None


@pytest.mark.asyncio
async def test_get_task_default_event_limit_keeps_twenty(task_manager_mock, event_manager_mock):
    """服务层默认仍为 EVENT_TIMELINE_LIMIT(20)——控制器/接口依赖，不可改。"""
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.get_task(team_id=1, task_id=1)

    assert taskService.EVENT_TIMELINE_LIMIT == 20
    assert result["success"] is True
    event_manager_mock.list_events.assert_awaited_once_with(1, limit=20)


@pytest.mark.asyncio
async def test_get_task_parameterizes_event_limit(task_manager_mock, event_manager_mock):
    """传入 event_limit 时按该值取事件（工具层默认 3 由工具层负责）。"""
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    await taskService.get_task(team_id=1, task_id=1, event_limit=1)

    event_manager_mock.list_events.assert_awaited_once_with(1, limit=1)


@pytest.mark.asyncio
async def test_get_task_reports_events_total(task_manager_mock, event_manager_mock):
    """响应携带 events_total（事件总数，跨 event_limit），用于判断是否还有更早事件。"""
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(return_value=[
        SimpleNamespace(id=3, task_id=1, actor_id=8, from_status=None, to_status=None, content="纯评论"),
    ])
    event_manager_mock.count_events = AsyncMock(return_value=9)

    result = await taskService.get_task(team_id=1, task_id=1, event_limit=1)

    assert result["task"]["events_total"] == 9
    assert len(result["task"]["events"]) == 1
    event_manager_mock.count_events.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_update_task_creator_cannot_approve_when_not_reviewer(task_manager_mock):
    """creator（≠reviewer）不能验收：REVIEWING→DONE 只认 is_reviewer，钉住「去 creator 兜底」。"""
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="DONE")

    assert result["success"] is False
    assert result["error_code"] == "permission_denied"
    task_manager_mock.update_task_status_with_event.assert_not_called()


# ---------------------------------------------------------------------------
# #67：update_task 改版（new_status / 内联评论）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_omit_new_status_creates_comment(task_manager_mock, event_manager_mock):
    """省略 new_status ⇒ 评论事件（from/to 均 NULL）、状态不变、action=commented。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.append_event = AsyncMock(return_value=SimpleNamespace(
        id=7, task_id=1, actor_id=99, from_status=None, to_status=None, content="外部补充",
    ))

    result = await taskService.update_task(team_id=1, caller_id=99, task_id=1, note="外部补充")

    assert result["success"] is True
    assert result["action"] == "commented"
    assert result["task"]["status"] == "IN_PROGRESS"
    task_manager_mock.update_task_status_with_event.assert_not_called()
    event_manager_mock.append_event.assert_awaited_once_with(
        1, 99, from_status=None, to_status=None, content="外部补充",
    )


@pytest.mark.asyncio
async def test_update_task_new_status_equal_current_is_comment(task_manager_mock, event_manager_mock):
    """new_status == 当前状态 ⇒ 归评论（不建自环边）。"""
    task = _build_task(status=TaskStatus.ON_HOLD, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.append_event = AsyncMock(return_value=SimpleNamespace(
        id=8, task_id=1, actor_id=11, from_status=None, to_status=None, content="仍搁置",
    ))

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="ON_HOLD", note="仍搁置")

    assert result["success"] is True
    assert result["action"] == "commented"
    task_manager_mock.update_task_status_with_event.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_comment_requires_non_empty_note(task_manager_mock, event_manager_mock):
    """评论分支 note 为空（含纯空白）⇒ 拒绝（empty_note）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.append_event = AsyncMock()

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, note="   ")

    assert result["success"] is False
    assert result["error_code"] == "empty_note"
    event_manager_mock.append_event.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_transition_returns_action_transitioned(task_manager_mock):
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="REVIEWING", note="提交")

    assert result["success"] is True
    assert result["action"] == "transitioned"


@pytest.mark.asyncio
async def test_update_task_terminal_rejects_transition(task_manager_mock):
    """终态 DONE 传新状态 ⇒ 拒绝流转（invalid_transition）。"""
    task = _build_task(status=TaskStatus.DONE, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)

    result = await taskService.update_task(team_id=1, caller_id=20, task_id=1, new_status="IN_PROGRESS")

    assert result["success"] is False
    assert result["error_code"] == "invalid_transition"
    task_manager_mock.update_task_status_with_event.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_terminal_allows_comment(task_manager_mock, event_manager_mock):
    """终态任务仅传 note ⇒ 允许追加评论。"""
    task = _build_task(status=TaskStatus.CANCELLED, assignee_id=11, creator_id=30, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.append_event = AsyncMock(return_value=SimpleNamespace(
        id=9, task_id=1, actor_id=20, from_status=None, to_status=None, content="结案分析",
    ))

    result = await taskService.update_task(team_id=1, caller_id=20, task_id=1, note="结案分析")

    assert result["success"] is True
    assert result["action"] == "commented"
    task_manager_mock.update_task_status_with_event.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_comment_allowed_for_any_team_member(task_manager_mock, event_manager_mock):
    """评论权限：团队内成员皆可（非 assignee / reviewer / creator 也可评论）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11, reviewer_id=20)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.append_event = AsyncMock(return_value=SimpleNamespace(
        id=10, task_id=1, actor_id=77, from_status=None, to_status=None, content="路过留言",
    ))

    result = await taskService.update_task(team_id=1, caller_id=77, task_id=1, note="路过留言")

    assert result["success"] is True
    assert result["action"] == "commented"


# ---------------------------------------------------------------------------
# #68：系统自动推进（依赖满足）+ 认领置位（D5=(a)）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_done_triggers_dependency_auto_progress(task_manager_mock, event_manager_mock):
    """流转到 DONE 后应触发依赖自动推进（以 SYSTEM 身份，对下游 PENDING 置 TODO）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    with patch("service.taskService.auto_progress_dependents", new=AsyncMock(return_value=[7])) as mock_prog:
        result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="DONE")

    assert result["action"] == "transitioned"
    mock_prog.assert_awaited_once_with(1, 1)


@pytest.mark.asyncio
async def test_update_task_non_done_does_not_trigger_auto_progress(task_manager_mock, event_manager_mock):
    """非 DONE 流转（如 IN_PROGRESS）不触发依赖推进。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    with patch("service.taskService.auto_progress_dependents", new=AsyncMock(return_value=[])) as mock_prog:
        result = await taskService.update_task(team_id=1, caller_id=11, task_id=1, new_status="IN_PROGRESS")

    assert result["action"] == "transitioned"
    mock_prog.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_progress_dependents_sets_todo_with_system_actor(task_manager_mock):
    """依赖全部 DONE 的 PENDING 下游 ⇒ 以 SYSTEM 身份置 TODO 并写系统事件。"""
    dep = _build_task(id=7, status=TaskStatus.PENDING, depends_on=[1])
    upstream = [_build_task(id=1, status=TaskStatus.DONE)]
    task_manager_mock.list_pending_dependents = AsyncMock(return_value=[dep])
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=upstream)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=dep)

    progressed = await taskService.auto_progress_dependents(1, 1)

    assert progressed == [7]
    kwargs = task_manager_mock.update_task_status_with_event.await_args.kwargs
    assert kwargs["actor_id"] == SpecialAgent.SYSTEM.value
    assert kwargs["from_status"] == TaskStatus.PENDING
    assert kwargs["to_status"] == TaskStatus.TODO


@pytest.mark.asyncio
async def test_auto_progress_dependents_holds_when_upstream_unfinished(task_manager_mock):
    """上游仍有未 DONE ⇒ 不下推。"""
    dep = _build_task(id=7, status=TaskStatus.PENDING, depends_on=[1, 2])
    upstream = [_build_task(id=1, status=TaskStatus.DONE), _build_task(id=2, status=TaskStatus.TODO)]
    task_manager_mock.list_pending_dependents = AsyncMock(return_value=[dep])
    task_manager_mock.get_tasks_by_ids = AsyncMock(return_value=upstream)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=dep)

    progressed = await taskService.auto_progress_dependents(1, 1)

    assert progressed == []
    task_manager_mock.update_task_status_with_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# #66：get_task_event（按事件 id 查单条事件）
# ---------------------------------------------------------------------------


def _build_event(**overrides):
    """构造事件 mock（SimpleNamespace，贴近 DAL 返回的行对象）。"""
    return SimpleNamespace(
        id=overrides.get("id", 5),
        task_id=overrides.get("task_id", 1),
        actor_id=overrides.get("actor_id", 11),
        from_status=overrides.get("from_status", TaskStatus.TODO),
        to_status=overrides.get("to_status", TaskStatus.IN_PROGRESS),
        content=overrides.get("content", "开工"),
    )


@pytest.mark.asyncio
async def test_get_task_event_not_found(task_manager_mock, event_manager_mock):
    """事件不存在 ⇒ 失败，且不查任务 / 不解析 actor。"""
    event_manager_mock.get_event = AsyncMock(return_value=None)

    result = await taskService.get_task_event(team_id=1, event_id=999)

    assert result["success"] is False
    assert "事件不存在" in result["message"]
    event_manager_mock.get_event.assert_awaited_once_with(999)
    task_manager_mock.get_task.assert_not_called()


@pytest.mark.asyncio
async def test_get_task_event_wrong_team_is_rejected(task_manager_mock, event_manager_mock):
    """跨团队事件 ⇒ 一律拒绝，且不泄露事件内容。"""
    event_manager_mock.get_event = AsyncMock(return_value=_build_event(task_id=1, content="机密"))
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=2))

    result = await taskService.get_task_event(team_id=1, event_id=5)

    assert result["success"] is False
    assert "事件不属于当前团队" in result["message"]
    assert "event" not in result
    assert "机密" not in str(result)


@pytest.mark.asyncio
async def test_get_task_event_success_serializes_and_resolves_display_name(task_manager_mock, event_manager_mock):
    """正常返回：复用 _serialize_event 字段（状态转字符串）+ actor_name（display_name）。"""
    event_manager_mock.get_event = AsyncMock(return_value=_build_event(id=5, task_id=1, actor_id=7))
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=1))

    def _fake_model_to_dict(obj, recurse=False):
        return {
            "id": obj.id,
            "task_id": obj.task_id,
            "actor_id": obj.actor_id,
            "content": obj.content,
        }

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_fake_model_to_dict):
        with patch("service.taskService.gtAgentManager") as mock_agents:
            mock_agents.get_agent_by_id = AsyncMock(return_value=SimpleNamespace(display_name="阿张"))
            result = await taskService.get_task_event(team_id=1, event_id=5)

    assert result["success"] is True
    event = result["event"]
    assert event["id"] == 5
    assert event["task_id"] == 1
    assert event["actor_id"] == 7
    assert event["from_status"] == "TODO"
    assert event["to_status"] == "IN_PROGRESS"
    assert event["content"] == "开工"
    assert event["actor_name"] == "阿张"
    mock_agents.get_agent_by_id.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_get_task_event_system_actor_name_is_system(task_manager_mock, event_manager_mock):
    """系统 actor（SpecialAgent.SYSTEM）⇒「系统」，且不去查 Agent 表。"""
    event_manager_mock.get_event = AsyncMock(
        return_value=_build_event(actor_id=SpecialAgent.SYSTEM.value, content="系统自动推进")
    )
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=1))

    with patch("service.taskService.gtAgentManager") as mock_agents:
        result = await taskService.get_task_event(team_id=1, event_id=5)

    assert result["event"]["actor_name"] == "系统"
    mock_agents.get_agent_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_get_task_event_operator_actor_name_is_operator(task_manager_mock, event_manager_mock):
    """人类 Operator（SpecialAgent.OPERATOR）⇒「Operator」。"""
    event_manager_mock.get_event = AsyncMock(return_value=_build_event(actor_id=SpecialAgent.OPERATOR.value))
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=1))

    with patch("service.taskService.gtAgentManager") as mock_agents:
        result = await taskService.get_task_event(team_id=1, event_id=5)

    assert result["event"]["actor_name"] == "Operator"
    mock_agents.get_agent_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_get_task_event_unknown_actor_falls_back_to_id(task_manager_mock, event_manager_mock):
    """Agent 不存在（已删除）⇒ 回退占位名「Agent#<id>」，不抛异常。"""
    event_manager_mock.get_event = AsyncMock(return_value=_build_event(actor_id=99))
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=1))

    with patch("service.taskService.gtAgentManager") as mock_agents:
        mock_agents.get_agent_by_id = AsyncMock(return_value=None)
        result = await taskService.get_task_event(team_id=1, event_id=5)

    assert result["event"]["actor_name"] == "Agent#99"


@pytest.mark.asyncio
async def test_claim_task_sets_in_progress_with_system_actor(task_manager_mock):
    """认领置位：TODO 的 assignee 认领 ⇒ TODO→IN_PROGRESS（SYSTEM 身份）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=11, reviewer_id=None)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    task_manager_mock.update_task_status_with_event = AsyncMock(return_value=task)

    res = await taskService.claim_task(11, 1)

    assert res == {"success": True, "action": "claimed"}
    kwargs = task_manager_mock.update_task_status_with_event.await_args.kwargs
    assert kwargs["actor_id"] == SpecialAgent.SYSTEM.value
    assert kwargs["from_status"] == TaskStatus.TODO
    assert kwargs["to_status"] == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_claim_task_skips_when_not_todo_or_not_assignee(task_manager_mock):
    """幂等 / 越权：非 TODO 或非 assignee ⇒ 跳过，不写事件。"""
    task_manager_mock.update_task_status_with_event = AsyncMock()

    # 已 IN_PROGRESS
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11))
    assert (await taskService.claim_task(11, 1))["action"] == "skipped"

    # 非 assignee
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(status=TaskStatus.TODO, assignee_id=11))
    assert (await taskService.claim_task(99, 1))["action"] == "skipped"

    task_manager_mock.update_task_status_with_event.assert_not_awaited()



# ---------------------------------------------------------------------------
# #65 返工：事件内容截断口径（面向 Agent 展示一律截断；get_task_event 例外）
# ---------------------------------------------------------------------------

# 远超 EVENT_CONTENT_MAX_CHARS(100) 的内容，用于截断断言。
_LONG_CONTENT = "打回原因：" + "很长的说明" * 30

# 截断后的期望值：前 N 字符 + 共享省略标记（全仓单一口径）。
_EXPECTED_TRUNCATED = _LONG_CONTENT[:EVENT_CONTENT_MAX_CHARS] + EVENT_TRUNCATED_SUFFIX


def _model_to_dict_with_content(obj, recurse=False):
    """model_to_dict 替身：保留 `content` 键（真实 DAL 行含 content，便于断言截断）。

    默认 autouse fixture 的替身不含 content 键，无法验证截断，故此处显式覆盖。
    """
    return {
        "id": getattr(obj, "id", None),
        "task_id": getattr(obj, "task_id", None),
        "actor_id": getattr(obj, "actor_id", None),
        "content": getattr(obj, "content", None),
        "from_status": getattr(obj, "from_status", None),
        "to_status": getattr(obj, "to_status", None),
    }


def test_serialize_event_truncates_by_default_and_flag_returns_full():
    """`_serialize_event` 默认截断 content；`truncate_content=False` 返回完整原文。"""
    event = _build_event(id=5, task_id=1, content=_LONG_CONTENT)

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_model_to_dict_with_content):
        truncated = taskService._serialize_event(event)
        full = taskService._serialize_event(event, truncate_content=False)

    assert len(_LONG_CONTENT) > EVENT_CONTENT_MAX_CHARS
    assert truncated["content"] == _EXPECTED_TRUNCATED
    assert truncated["content"] != _LONG_CONTENT
    assert full["content"] == _LONG_CONTENT


@pytest.mark.asyncio
async def test_get_task_truncates_long_event_content(task_manager_mock, event_manager_mock):
    """面向 Agent 的 get_task（=get_task_detail 路径）单条事件内容超过 100 字符 ⇒ 截断。

    旧实现（直接回传完整 content）在本用例下必红。
    """
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(return_value=[
        SimpleNamespace(
            id=5, task_id=1, actor_id=8,
            from_status=TaskStatus.IN_PROGRESS, to_status=TaskStatus.IN_PROGRESS,
            content=_LONG_CONTENT,
        ),
    ])

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_model_to_dict_with_content):
        result = await taskService.get_task(team_id=1, task_id=1)

    assert result["success"] is True
    assert result["task"]["events"][0]["content"] == _EXPECTED_TRUNCATED


@pytest.mark.asyncio
async def test_get_task_truncates_last_note_and_current_block_reason(
    task_manager_mock, event_manager_mock
):
    """派生字段 last_note / current_block_reason 同口径截断（它们也是事件 content）。"""
    task = _build_task(id=1, team_id=1, status=TaskStatus.ON_HOLD)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(return_value=[
        SimpleNamespace(
            id=5, task_id=1, actor_id=8,
            from_status=TaskStatus.IN_PROGRESS, to_status=TaskStatus.ON_HOLD,
            content=_LONG_CONTENT,
        ),
    ])

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_model_to_dict_with_content):
        result = await taskService.get_task(team_id=1, task_id=1)

    assert result["task"]["last_note"] == _EXPECTED_TRUNCATED
    assert result["task"]["current_block_reason"] == _EXPECTED_TRUNCATED


@pytest.mark.asyncio
async def test_list_task_events_truncates_long_event_content(
    task_manager_mock, event_manager_mock
):
    """list_task_events 与 get_task 共用同一截断口径（不各写一套）。"""
    task = _build_task(id=1, team_id=1)
    task_manager_mock.get_task = AsyncMock(return_value=task)
    event_manager_mock.list_events = AsyncMock(return_value=[
        SimpleNamespace(
            id=5, task_id=1, actor_id=8,
            from_status=None, to_status=None,
            content=_LONG_CONTENT,
        ),
    ])
    event_manager_mock.count_events = AsyncMock(return_value=1)

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_model_to_dict_with_content):
        result = await taskService.list_task_events(team_id=1, task_id=1)

    assert result["events"][0]["content"] == _EXPECTED_TRUNCATED


@pytest.mark.asyncio
async def test_get_task_event_keeps_full_content_as_counterpart(
    task_manager_mock, event_manager_mock
):
    """守卫 #66：同一条超长事件，get_task_event 必须返回**完整原文**（唯一不截断入口）。"""
    event_manager_mock.get_event = AsyncMock(
        return_value=_build_event(id=5, task_id=1, actor_id=7, content=_LONG_CONTENT)
    )
    task_manager_mock.get_task = AsyncMock(return_value=_build_task(id=1, team_id=1))

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_model_to_dict_with_content):
        with patch("service.taskService.gtAgentManager") as mock_agents:
            mock_agents.get_agent_by_id = AsyncMock(return_value=SimpleNamespace(display_name="阿张"))
            result = await taskService.get_task_event(team_id=1, event_id=5)

    assert result["event"]["content"] == _LONG_CONTENT
    assert result["event"]["content"] != _EXPECTED_TRUNCATED
