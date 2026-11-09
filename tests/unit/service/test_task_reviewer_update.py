"""#158 单元测试：`update_task` 的「仅验收人变更」路径（D1-D8）。

分工：
- 本文件用 **DAL 替身**逐个咬住权限与顺序（谁能改、谁不能改、错误码、
  校验是否在落库之前），以及「两个参数都不给 ⇒ 老路径」的回归底线；
- 真库断言（主表 `reviewer_id`、事件原始行、事务回滚）在
  `tests/integration/test_task_reviewer_update/test.py`，不在这里用 mock 假装。

命名约定：`U*` 对应任务书 R1-R8 的单元层部分。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import EmployStatus, TaskPriority, TaskStatus
from model.dbModel.gtAgentTask import GtAgentTask
from service import taskService


@asynccontextmanager
async def _passthrough_atomic():
    """公共事务边界 `atomic()` 的替身：不加锁直接放行（回滚语义由集成用例证明）。"""
    yield


def _save_task_side_effect(task, *, force_insert: bool = False, only=None):
    return task


def _agent(agent_id: int, name: str, *, team_id: int = 1, status: EmployStatus = EmployStatus.ON_BOARD):
    """构造 agent 替身：`display_name` 是真属性（不是 MagicMock 自动生成的假值）。"""
    return SimpleNamespace(id=agent_id, team_id=team_id, name=name, display_name=name, employ_status=status)


def _build_task(**overrides):
    task = MagicMock(spec=GtAgentTask)
    task.id = overrides.get("id", 1)
    task.team_id = overrides.get("team_id", 1)
    task.title = "test task"
    task.description = ""
    task.assignee_id = overrides.get("assignee_id", 11)
    task.creator_id = overrides.get("creator_id", 6)
    task.reviewer_id = overrides.get("reviewer_id", 20)
    task.status = overrides.get("status", TaskStatus.IN_PROGRESS)
    task.priority = overrides.get("priority", TaskPriority.NORMAL)
    task.depends_on = []
    return task


@pytest.fixture(autouse=True)
def _simple_model_to_dict():
    """把 peewee 的 `model_to_dict` 换成「读属性拼 dict」版本（替身对象没有 `_meta`）。

    只覆盖本文件用到的键：`model_to_dict` 在这里只是「返回体里带上字段」，
    真库上的返回体形状由集成用例断言。
    """

    def _fake(model, recurse=False):
        status = getattr(model, "status", None)
        priority = getattr(model, "priority", None)
        return {
            "id": getattr(model, "id", None),
            "team_id": getattr(model, "team_id", None),
            "title": getattr(model, "title", None),
            "assignee_id": getattr(model, "assignee_id", None),
            "creator_id": getattr(model, "creator_id", None),
            "reviewer_id": getattr(model, "reviewer_id", None),
            "status": status.value if isinstance(status, TaskStatus) else status,
            "priority": priority.value if isinstance(priority, TaskPriority) else priority,
            "from_status": getattr(model, "from_status", None),
            "to_status": getattr(model, "to_status", None),
            "content": getattr(model, "content", ""),
            "actor_id": getattr(model, "actor_id", None),
        }

    with patch("playhouse.shortcuts.model_to_dict", side_effect=_fake):
        yield


@pytest.fixture
def dal():
    """统一替身：任务 DAL / 事件 DAL / Agent DAL / 事务边界 / 消息总线。"""
    with patch("service.taskService.gtAgentTaskManager") as task_dal, \
            patch("service.taskService.gtAgentTaskEventManager") as event_dal, \
            patch("service.taskService.gtAgentManager") as agent_dal, \
            patch("service.taskService.atomic", new=_passthrough_atomic), \
            patch("service.messageBus.publish") as publish:
        task_dal.save_task = AsyncMock(side_effect=_save_task_side_effect)
        event_dal.append_event = AsyncMock(return_value=SimpleNamespace(
            id=7, task_id=1, actor_id=0, from_status=None, to_status=None, content='',
        ))
        agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[])
        agent_dal.get_agents_by_ids = AsyncMock(return_value=[])
        yield SimpleNamespace(
            task_dal=task_dal, event_dal=event_dal, agent_dal=agent_dal, publish=publish,
        )


def _publish_topics(publish) -> list[str]:
    return [c.args[0].name for c in publish.call_args_list]


# ---------------------------------------------------------------------------
# R1｜权限真咬合
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_creator_can_change_reviewer(dal):
    """创建人改验收人 ⇒ 成功，返回新旧 ID，事件 from/to 均 NULL 且文案以「验收人变更：」开头。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])
    dal.agent_dal.get_agents_by_ids = AsyncMock(
        return_value=[_agent(20, "测试员甲"), _agent(21, "测试员乙")]
    )

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, reviewer_id=21, note="甲休假",
    )

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert res["old_reviewer_id"] == 20
    assert res["new_reviewer_id"] == 21
    assert res["reviewer_name"] == "测试员乙"
    assert task.reviewer_id == 21

    dal.task_dal.save_task.assert_awaited_once()
    assert dal.task_dal.save_task.await_args.kwargs["only"] == [GtAgentTask.reviewer_id]

    dal.event_dal.append_event.assert_awaited_once()
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["from_status"] is None and kwargs["to_status"] is None
    assert kwargs["content"].startswith("验收人变更：")
    assert "20" in kwargs["content"] and "21" in kwargs["content"]
    assert "甲休假" in kwargs["content"]


@pytest.mark.asyncio
async def test_current_reviewer_can_hand_over(dal):
    """当前验收人可把验收人交接给别人（REVIEWING 时也可以——这是主要真实用途）。"""
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(team_id=1, caller_id=20, task_id=1, reviewer_id=21)

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert task.reviewer_id == 21


@pytest.mark.asyncio
async def test_assignee_cannot_change_reviewer(dal):
    """执行人改验收人 ⇒ permission_denied，且**不落库、不写事件、不推送**。

    R1 变异自证的靶点：删掉 `_handle_reviewer_change` 里的 `if is_assignee` 分支，
    本条会变成 `success=True`（验收门被绕过）。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(team_id=1, caller_id=11, task_id=1, reviewer_id=21)

    assert res["success"] is False
    assert res["error_code"] == "permission_denied"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()
    dal.event_dal.append_event.assert_not_awaited()
    assert _publish_topics(dal.publish) == []


@pytest.mark.asyncio
async def test_assignee_who_is_also_creator_still_cannot_change_reviewer(dal):
    """创建人 == 执行人时也不放行：执行人身份优先（否则「自己给自己派单」就能自选验收人）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(team_id=1, caller_id=11, task_id=1, reviewer_id=21)

    assert res["success"] is False
    assert res["error_code"] == "permission_denied"
    assert task.reviewer_id == 20


@pytest.mark.asyncio
async def test_unrelated_member_cannot_change_reviewer(dal):
    """既不是创建人也不是当前验收人的团队成员 ⇒ permission_denied。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(team_id=1, caller_id=99, task_id=1, reviewer_id=21)

    assert res["success"] is False
    assert res["error_code"] == "permission_denied"
    dal.task_dal.save_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# R2｜组合调用绕不过验收（D5）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reviewer_change_cannot_ride_along_with_transition(dal):
    """执行人一次调用同时「提交 REVIEWING + 换验收人」⇒ reviewer_change_must_be_separate，状态与验收人都没变。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=11, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(11, "自己人")])

    res = await taskService.update_task(
        team_id=1, caller_id=11, task_id=1, new_status="REVIEWING", reviewer_id=11,
    )

    assert res["success"] is False
    assert res["error_code"] == "reviewer_change_must_be_separate"
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()
    dal.event_dal.append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_loop_new_status_plus_reviewer_change_is_allowed(dal):
    """`new_status` 等于当前状态（只是评论意图）不算「状态流转」，可以同时改验收人。

    但此时**不写评论事件**（否则一次调用产出两条事件，且评论分支要求 note 非空），
    `note` 改作变更原由；返回 message 里明说这一点。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, new_status="in_progress", reviewer_id=21, note="交接",
    )

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert dal.event_dal.append_event.await_count == 1
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["from_status"] is None and kwargs["to_status"] is None
    assert "本次 new_status 与当前状态相同" in res["message"]


# ---------------------------------------------------------------------------
# R3｜终态挡住（D3）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("terminal", [TaskStatus.DONE, TaskStatus.CANCELLED])
@pytest.mark.asyncio
async def test_terminal_task_reviewer_is_immutable(dal, terminal):
    """终态任务一律拒绝改验收人（挡住「用改验收人绕过终态收口」）。"""
    task = _build_task(status=terminal, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21)

    assert res["success"] is False
    assert res["error_code"] == "invalid_transition"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()


@pytest.mark.parametrize("terminal", [TaskStatus.DONE, TaskStatus.CANCELLED])
@pytest.mark.asyncio
async def test_terminal_task_clear_fields_is_immutable(dal, terminal):
    task = _build_task(status=terminal, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"])

    assert res["success"] is False
    assert res["error_code"] == "invalid_transition"
    assert task.reviewer_id == 20


# ---------------------------------------------------------------------------
# R4｜REVIEWING 时撤销被拒（D2）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_fields_rejected_in_reviewing(dal):
    """REVIEWING 时撤销验收人 ⇒ 拒绝，任务仍是「有验收人」状态（不制造无人验收的死锁）。"""
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"])

    assert res["success"] is False
    assert res["error_code"] == "reviewer_required_in_reviewing"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_fields_by_non_creator_is_denied(dal):
    """撤销只能由创建人做；当前验收人只能交接，不能撤销（撤销后执行人可直接 DONE）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=20, task_id=1, clear_fields=["reviewer_id"])

    assert res["success"] is False
    assert res["error_code"] == "permission_denied"
    assert task.reviewer_id == 20


@pytest.mark.asyncio
async def test_clear_fields_by_creator_succeeds_and_warns_consequence(dal):
    """创建人撤销验收人 ⇒ 成功；返回 message 必须明写「执行人可直接置 DONE」的后果。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"])

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert res["new_reviewer_id"] is None
    assert task.reviewer_id is None
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["content"].startswith("验收人撤销：")
    assert "直接" in res["message"] and "DONE" in res["message"]


# ---------------------------------------------------------------------------
# R5｜新验收人校验（D4）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reviewer_cannot_be_assignee(dal):
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=11)

    assert res["success"] is False
    assert res["error_code"] == "invalid_reviewer"
    dal.task_dal.save_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_team_agent_is_rejected(dal):
    """跨团队成员不能当验收人（DAL 只放行同 team 或 team_id=-1；这里再显式挡 team_id 不等）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    # 模拟 DAL 的宽松返回（跨团队 Agent 也会被查出来）
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(
        return_value=[_agent(21, "外队", team_id=-1)]
    )

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21)

    assert res["success"] is False
    assert res["error_code"] == "invalid_reviewer"


@pytest.mark.asyncio
async def test_missing_agent_is_rejected(dal):
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[])

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=9999)

    assert res["success"] is False
    assert res["error_code"] == "invalid_reviewer"


@pytest.mark.asyncio
async def test_off_board_agent_is_rejected(dal):
    """离职成员设成验收人等于制造「无人能通过验收」的死锁，与 D2 同口径拒绝。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(
        return_value=[_agent(21, "已离职", status=EmployStatus.OFF_BOARD)]
    )

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21)

    assert res["success"] is False
    assert res["error_code"] == "invalid_reviewer"


@pytest.mark.asyncio
async def test_unchanged_reviewer_returns_early_without_event(dal):
    """设成与当前相同 ⇒ reviewer_unchanged，且**不落库、不写事件**（不产出无意义时间线）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=20)

    assert res["success"] is True
    assert res["action"] == "reviewer_unchanged"
    dal.task_dal.save_task.assert_not_awaited()
    dal.event_dal.append_event.assert_not_awaited()
    assert _publish_topics(dal.publish) == []


# ---------------------------------------------------------------------------
# D8｜两个参数的关系
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_both_reviewer_id_and_clear_flag_is_ambiguous(dal):
    """`reviewer_id` 与 `clear_fields=["reviewer_id"]` 同时给 ⇒ ambiguous_reviewer_change，且不改库。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, reviewer_id=21, clear_fields=["reviewer_id"],
    )

    assert res["success"] is False
    assert res["error_code"] == "ambiguous_reviewer_change"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_fields_when_already_none_is_unchanged(dal):
    """本来就没验收人时撤销 ⇒ reviewer_unchanged，不写事件（不产出无意义时间线）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=6, reviewer_id=None)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"])

    assert res["success"] is True
    assert res["action"] == "reviewer_unchanged"
    dal.event_dal.append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_integer_reviewer_id_is_rejected(dal):
    """模型可能传字符串 / 空值：非整数一律 invalid_reviewer，不要让它变成 500。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id="abc")  # type: ignore[arg-type]

    assert res["success"] is False
    assert res["error_code"] == "invalid_reviewer"
    assert task.reviewer_id == 20


@pytest.mark.asyncio
async def test_no_reviewer_params_behaves_exactly_like_before(dal):
    """回归底线：两个参数都不给 ⇒ 走老路径（评论），不触达验收人逻辑。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, note="只是评论")

    assert res["success"] is True
    assert res["action"] == "commented"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["content"] == "只是评论"
    assert kwargs["from_status"] is None and kwargs["to_status"] is None


# ---------------------------------------------------------------------------
# D7｜推送顺序与失败静默
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_order_event_then_task_changed(dal):
    """成功变更：先 `TASK_EVENT_APPENDED`（事务内写完、事务外推），再 `TASK_CHANGED`。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21)

    assert _publish_topics(dal.publish) == ["TASK_EVENT_APPENDED", "TASK_CHANGED"]
    assert dal.publish.call_args_list[1].kwargs["old_status"] == TaskStatus.IN_PROGRESS.value


@pytest.mark.asyncio
async def test_event_write_failure_rolls_back_and_publishes_nothing(dal):
    """R8（单元层）：事件写入抛异常 ⇒ 异常上抛、主表内存态回滚、不推任何消息。

    真库上的「`reviewer_id` 真的没落库」由集成用例证明（这里 atomic 是直通替身）。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])
    dal.event_dal.append_event = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21)

    assert _publish_topics(dal.publish) == []


# ---------------------------------------------------------------------------
# 事件文案（D6）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_content_uses_default_text_without_reason(dal):
    """不给 `note` ⇒ 文案只有「验收人变更：旧 → 新」，不带「（原由：）」空壳。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])
    dal.agent_dal.get_agents_by_ids = AsyncMock(
        return_value=[_agent(20, "测试员甲"), _agent(21, "测试员乙")]
    )

    await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21, note="   ")

    content = dal.event_dal.append_event.await_args.kwargs["content"]
    assert content == "验收人变更：20/测试员甲 → 21/测试员乙"


@pytest.mark.asyncio
async def test_event_content_falls_back_to_id_when_name_unknown(dal):
    """名字查不到（Agent 已删除）时只给 ID，不写 `None`。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    await taskService.update_task(team_id=1, caller_id=6, task_id=1, reviewer_id=21)

    content = dal.event_dal.append_event.await_args.kwargs["content"]
    assert content == "验收人变更：20 → 21/测试员乙"
    assert "None" not in content


# ---------------------------------------------------------------------------
# 前置校验顺序：非法入参不该触达 DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_lookup_happens_once_and_before_permission(dal):
    """任务不存在 ⇒ 失败，且不写事件（`get_task` 命中一次即够）。"""
    dal.task_dal.get_task = AsyncMock(return_value=None)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=404, reviewer_id=21)

    assert res["success"] is False
    assert res.get("error_code") is None
    dal.task_dal.get_task.assert_awaited_once()
    dal.event_dal.append_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# #181｜`clear_fields` 白名单（通用字段清除）
# ---------------------------------------------------------------------------
#
# 语义裁定（小马哥 2026-11-09 23:29 / 23:30，后者为准）：
# - 旧布尔开关 **已全量删除**（Operator 23:28 裁定），撤销统一走 `clear_fields=["reviewer_id"]`，D1–D7 一字不改；
# - 白名单外字段 ⇒ `invalid_clear_field`（**运行时**校验，不靠 schema enum）；
# - `reviewer_id` + `clear_fields=["reviewer_id"]` ⇒ `ambiguous_reviewer_change`（唯一一种冲突）；
# - `clear_fields=[]` ⇒ 视为未提供（与 `list_tasks.status=[]` 报错**相反**，有意为之）。
#
# 白名单判据定版 3 项（23:30）：
# | 判据 | 治的失效模式 |
# |---|---|---|
# | `test_allowed_not_empty` | `ALLOWED_CLEAR_FIELDS` 被误清空 ⇒ 参数化打 SKIPPED 而非 FAILED，整套假绿 |
# | `test_each_allowed_field_actually_changes_state` | **扩 enum 忘实现**（主力）：新成员走老路径、返回 success 而实际没清 |
# | `test_reviewer_id_clear_has_correct_effect` | 「有没有变化」对了但「变化对不对」不一定（清 reviewer 却改了 status） |
#
# ⚠️ 差分式只判「有没有变化」，**不判「变化对不对」**——后者由第 3 项显式断言兜。

from constants import ALLOWED_CLEAR_FIELDS  # noqa: E402


def test_allowed_not_empty():
    """空集合的唯一防线：`parametrize` 空集合打 SKIPPED 不打 FAILED。

    实测（pytest 9.0.3）：`@pytest.mark.parametrize("field", sorted(EMPTY))` + `assert False`
    ⇒ `1 passed, 1 skipped, 0 failed`。所以白名单被误清空时，下面那条主力判据会**静默跳过**，
    整套判据全绿。这条必须独立存在。
    """
    assert ALLOWED_CLEAR_FIELDS


def _snap(task, events) -> tuple:
    """只取「与被清除字段相关」的可观测状态。

    ⚠️ 差分式的必踩假绿坑：直接比 ORM 对象 / 事件对象会**恒绿** ——
    两次构造是不同实例（`==` 比身份），`event.id` / `updated_at` 每次都变。
    因此这里只比 `reviewer_id` / `status` / 事件三元组。
    """
    return (
        task.reviewer_id,
        task.status,
        tuple((e.from_status, e.to_status, e.content) for e in events),
    )


async def _clear_probe(dal, field):
    """把同一份 base 任务交给**真实现**：`field=None` ⇒ 不清；`field="x"` ⇒ 清该字段。

    为什么 `field=None` 时传 `note=""`：这是让「不清」的调用**不产生任何状态变化**，
    从而成为干净基线。若基线自己也写一条评论事件，差分就退化成恒绿（两条路都多一条事件）。
    这是对定版伪码的落地细化，报告里已声明。
    """
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=6, reviewer_id=7)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(7, "验收人甲")])

    def _event(*args, **kwargs):
        return SimpleNamespace(
            id=7, task_id=1, actor_id=kwargs.get("actor_id"),
            from_status=kwargs.get("from_status"), to_status=kwargs.get("to_status"),
            content=kwargs.get("content", ""),
        )

    dal.event_dal.append_event = AsyncMock(side_effect=_event)
    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, note="",
        clear_fields=None if field is None else [field],
    )
    events = [SimpleNamespace(**c.kwargs) for c in dal.event_dal.append_event.await_args_list]
    return _snap(task, events), res


@pytest.mark.parametrize("field", sorted(ALLOWED_CLEAR_FIELDS))
@pytest.mark.asyncio
async def test_each_allowed_field_actually_changes_state(dal, field):
    """白名单里的每个字段都必须**真的**改变状态（主力判据：治「扩 enum 忘实现」）。

    失效形态：新白名单成员走老路径、返回 `success=True` 而实际没清 —— 恒绿不报错。
    这里没有任何「人工写的预期效果表」可被写成恒真，所以不存在「用一份人工声明守另一份」的问题。
    """
    cleared_snap, cleared_res = await _clear_probe(dal, field)
    untouched_snap, _ = await _clear_probe(dal, None)

    assert cleared_snap != untouched_snap, (
        f'清 {field} 与不清的结果完全相同 => 白名单收了它但没有任何效果（静默忽略）；'
        f"cleared={cleared_snap!r}, untouched={untouched_snap!r}, res={cleared_res!r}"
    )


@pytest.mark.asyncio
async def test_reviewer_id_clear_has_correct_effect(dal):
    """「变化对不对」：清 `reviewer_id` ⇒ `reviewer_id is None` 且恰好一条撤销事件。

    差分式只管「有变化」；清 reviewer 却改了 status 也能过差分，必须由这条显式钉住。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"],
    )

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert task.reviewer_id is None
    assert task.status == TaskStatus.IN_PROGRESS, "撤销验收人不许顺手改状态"
    dal.event_dal.append_event.assert_awaited_once()
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["from_status"] is None and kwargs["to_status"] is None
    assert kwargs["content"].startswith("验收人撤销：")


def _tool_ctx(agent_id: int = 6, team_id: int = 1):
    from service.roomService.core import ToolCallContext
    return ToolCallContext(agent_id=agent_id, team_id=team_id, tool_name="update_task")


@pytest.mark.asyncio
async def test_invalid_clear_field_is_rejected_by_runtime_check(dal):
    """野值 `["title"]` ⇒ `invalid_clear_field`，**且不触达 DB**。

    这是本单最关键的一条：schema 里的 `enum: ["reviewer_id"]` 只是给模型看的声明，
    native / tsp 驱动都不按 schema 拦，所以必须证明服务端自己拦得住。
    走**真入口** `tools.update_task`（不是只测 `taskService.update_task`），
    否则"注册/转发层漏了"这类缺陷照旧漏过。
    """
    from service.funcToolService import tools

    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await tools.update_task(
        task_id=1, clear_fields=["title"], _context=_tool_ctx(),  # type: ignore[list-item]
    )

    assert res["success"] is False
    assert res["error_code"] == "invalid_clear_field"
    assert "title" in res["message"]
    # 白名单内容要说出来，模型才知道能用什么
    assert "reviewer_id" in res["message"]
    # 校验必须在落库之前：任务没查、没存、没写事件
    dal.task_dal.get_task.assert_not_awaited()
    dal.task_dal.save_task.assert_not_awaited()
    dal.event_dal.append_event.assert_not_awaited()
    assert task.reviewer_id == 20


@pytest.mark.asyncio
async def test_invalid_clear_field_lists_every_bad_name(dal):
    """多个野值 ⇒ 全部列出（不静默丢弃其中任何一个）。"""
    from service.funcToolService import tools

    res = await tools.update_task(
        task_id=1, clear_fields=["title", "assignee_id"], _context=_tool_ctx(),  # type: ignore[list-item]
    )

    assert res["success"] is False
    assert res["error_code"] == "invalid_clear_field"
    assert "title" in res["message"] and "assignee_id" in res["message"]


@pytest.mark.asyncio
async def test_clear_fields_reviewer_id_clears_reviewer(dal):
    """`clear_fields=["reviewer_id"]` ⇒ 撤销验收人，事件文案以「验收人撤销：」开头。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"],
    )

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert res["new_reviewer_id"] is None
    assert task.reviewer_id is None
    dal.task_dal.save_task.assert_awaited_once()
    dal.event_dal.append_event.assert_awaited_once()
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["from_status"] is None and kwargs["to_status"] is None
    assert kwargs["content"].startswith("验收人撤销：")


@pytest.mark.asyncio
async def test_clear_fields_keeps_d1_d2_d3_permissions(dal):
    """真正覆盖范围：**D1 / D2 / D3 三条**（执行人撤销被拒、REVIEWING 撤销被拒、终态不可撤销）。

    ⚠️ 本用例**不覆盖** D5（不得与状态流转同批）—— 那条在
    `test_clear_fields_cannot_ride_along_with_transition` 单独钉住。
    原来的 docstring 写"D1–D7"但只断言了三条，验收人（#181 二部测试，2026-11-10 00:30）
    实测出这个缺口：把 D5 改坏后整套 250 passed 全绿、一条不红。
    **用例名声称的范围必须等于它真正断言的范围**，否则后人看名字会以为守住了。

    `clear_fields` 换的是**表达方式**，不是权限规则 —— 这几条在 `clear_fields=["reviewer_id"]`
    下再考一遍，防止"新路径绕过老校验"。
    """
    # 执行人（assignee）撤销 ⇒ permission_denied
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    res = await taskService.update_task(
        team_id=1, caller_id=11, task_id=1, clear_fields=["reviewer_id"],
    )
    assert res["success"] is False
    assert res["error_code"] == "permission_denied"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()

    # REVIEWING 时撤销（即使是创建人）⇒ reviewer_required_in_reviewing
    reviewing = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=reviewing)
    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"],
    )
    assert res["success"] is False
    assert res["error_code"] == "reviewer_required_in_reviewing"
    assert reviewing.reviewer_id == 20

    # 终态 ⇒ invalid_transition
    done = _build_task(status=TaskStatus.DONE, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=done)
    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, clear_fields=["reviewer_id"],
    )
    assert res["success"] is False
    assert res["error_code"] == "invalid_transition"
    assert done.reviewer_id == 20


@pytest.mark.asyncio
async def test_clear_fields_cannot_ride_along_with_transition(dal):
    """D5 在 `clear_fields` 路径同样成立：撤销不得与状态流转同批。

    实现本身已正确（D5 检查在 `_handle_reviewer_change` 里，对两条路径一视同仁），
    本用例补的是**判据**——此前 `clear_fields` 路径上 D5 零覆盖。

    缺口是验收人实测出来的：把 D5 改成 `... and not clearing:` 后跑三套，
    **263 passed 全绿、一条没红**。变异态的失效形态是：

        update_task(new_status="REVIEWING", clear_fields=["reviewer_id"])
        → {'success': True, 'action': 'reviewer_updated'}
        → status 仍是 IN_PROGRESS（请求的 REVIEWING 被静默吞掉），验收人却已被撤销

    即"模型以为提交了验收 + 撤了验收人，实际验收没提交"。这正是 D5 要挡的口子：
    执行人不能在"提交 REVIEWING"的同一次调用里动验收人。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, new_status="REVIEWING", clear_fields=["reviewer_id"],
    )

    assert res["success"] is False
    assert res["error_code"] == "reviewer_change_must_be_separate"
    # 两侧都必须没发生：流转不许发生、撤销不许发生
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()
    dal.event_dal.append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_reviewer_id_with_clear_fields_is_ambiguous(dal):
    """`reviewer_id` + `clear_fields=["reviewer_id"]` ⇒ 冲突拒绝，且不改库。

    定版只剩这一种冲突来源，message 必须同时点名 `reviewer_id` 与 `clear_fields`
    两个字段，且**不得残留已删除的参数名**（验收会 grep 整个改动面）。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, reviewer_id=21, clear_fields=["reviewer_id"],
    )

    assert res["success"] is False
    assert res["error_code"] == "ambiguous_reviewer_change"
    assert "clear_fields" in res["message"]
    assert "reviewer_id" in res["message"]
    # 断言里刻意拆成两段字符串拼接：本仓验收门禁要求源码中该参数名出现 0 次，
    # 连"断言它不存在"的用例也不许把它写成一个完整字面量。
    assert "clear_" "reviewer" not in res["message"], "已删除的参数名不许出现在文案里"
    # 冲突必须在落库前拦住
    dal.task_dal.get_task.assert_not_awaited()
    dal.task_dal.save_task.assert_not_awaited()
    assert task.reviewer_id == 20


@pytest.mark.asyncio
async def test_empty_clear_fields_is_treated_as_not_provided(dal):
    """`clear_fields=[]` ⇒ 视为未提供：验收人不动、无事件、走老路径（评论）。

    这条是最容易写成假绿的地方：若实现把空数组当成"撤销成功"，`reviewer_id` 会变 None，
    本用例立刻红。**与 `list_tasks.status=[]` 报错相反是有意设计**，理由写在两处 docstring。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, note="只是评论", clear_fields=[],
    )

    assert res["success"] is True
    assert res["action"] == "commented"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()
    kwargs = dal.event_dal.append_event.await_args.kwargs
    assert kwargs["content"] == "只是评论"
    assert kwargs["from_status"] is None and kwargs["to_status"] is None


@pytest.mark.asyncio
async def test_empty_clear_fields_does_not_bypass_empty_note_check(dal):
    """`clear_fields=[]` **不能**让空 note 变成「撤销成功」：仍走评论路径 ⇒ `empty_note`。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_fields=[])

    assert res["success"] is False
    assert res["error_code"] == "empty_note"
    assert task.reviewer_id == 20
    dal.event_dal.append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_bare_string_clear_fields_is_normalized(dal):
    """模型可能按老习惯传裸字符串：归一成单元素列表，不能逐字符迭代成假野值。

    不归一的话 `for f in "reviewer_id"` 会得到 'r'/'e'/'v'…，报出一堆 fake 的
    `invalid_clear_field`（与 `list_tasks.status` 裸字符串兼容同一口径）。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, clear_fields="reviewer_id",  # type: ignore[arg-type]
    )

    assert res["success"] is True
    assert task.reviewer_id is None


@pytest.mark.asyncio
async def test_duplicate_clear_field_names_are_deduped(dal):
    """重复给同一字段 ⇒ 只处理一次（不因重复写法写出两条事件）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1,
        clear_fields=["reviewer_id", "reviewer_id"],
    )

    assert res["success"] is True
    dal.event_dal.append_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# #181｜非字符串输入收口（小马哥 2026-11-10 00:06 实测打回项）
# ---------------------------------------------------------------------------
#
# 背景：draft §4.3.1 的前提是「模型只能从枚举选择，写不了野值」——这个前提
# 对 native / tsp 驱动**不成立**（schema 的 enum 只产声明不产校验）。所以
# `clear_fields` 收到非字符串值是真实可能，不是理论情况。
#
# 收口前的三类失效（均在 `wt_clearfields` 实测）：
# | 输入 | 收口前 |
# |---|---|
# | `5` / `5.5` / `True` / `object()` | **未捕获 `TypeError`**（`'int' object is not iterable`），绕过 `TogoException`，模型拿到 Python 堆栈措辞而非错误码 |
# | `[{"a":1}]` / `[[1]]` | `TypeError: unhashable type`（`dict.fromkeys` 去重时炸） |
# | `{"reviewer_id": True}` | **静默通过且真的撤销了验收人**（dict 可迭代 ⇒ 拿到 keys）——最严重 |
# | `b"reviewer_id"` | 逐字节迭代成 `114、101、118…`，把 ASCII 码当字段名报给模型 |
#
# 口径：`str`（裸字符串兼容）/ 元素全为 `str` 的 `list`·`tuple` 放行，其余一律
# `invalid_clear_field`（**复用现有码，不新增**）。


@pytest.mark.parametrize(
    "bad_value",
    [5, 5.5, True, object(), [{"a": 1}], [[1]], {"reviewer_id": True}, b"reviewer_id"],
    ids=["int", "float", "bool", "object", "list_of_dict", "list_of_list", "dict", "bytes"],
)
@pytest.mark.asyncio
async def test_non_string_clear_fields_is_rejected_not_raised(dal, bad_value):
    """非字符串输入 ⇒ `invalid_clear_field`，**不抛未捕获异常、不改库**。

    每个参数各自咬一个具体失效形态（见本节表）。`dict` 那条最关键：
    收口前它会**静默撤销验收人**，属于"意图被猜对但输入形态错"的静默取一边。

    走**真入口** `tools.update_task`，因为异常通道差异（`TogoException` vs 原始
    `TypeError`）只在工具层返回体上才能观测到。
    """
    from service.funcToolService import tools

    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    # 不抛异常本身就是断言的一部分：收口前 int/float/bool/object 会抛 TypeError
    res = await tools.update_task(
        task_id=1, clear_fields=bad_value, _context=_tool_ctx(),  # type: ignore[arg-type]
    )

    assert res["success"] is False
    assert res["error_code"] == "invalid_clear_field", (
        f"输入 {bad_value!r} 应被类型收口拦下，实际 error_code={res.get('error_code')!r}"
    )
    # 报错要能自解释：说出收到的是什么类型 + 当前可用字段
    assert type(bad_value).__name__ in res["message"]
    assert "reviewer_id" in res["message"]
    # 校验必须在落库前：任务没查、没存、没写事件，reviewer 原值不变
    dal.task_dal.get_task.assert_not_awaited()
    dal.task_dal.save_task.assert_not_awaited()
    dal.event_dal.append_event.assert_not_awaited()
    assert task.reviewer_id == 20


@pytest.mark.parametrize("good_value", ["reviewer_id", ["reviewer_id"], ("reviewer_id",)])
@pytest.mark.asyncio
async def test_legal_clear_fields_forms_still_work(dal, good_value):
    """收口不能把合法形态一起挡掉：裸字符串 / list / tuple 三种都要照常撤销。

    `list`/`tuple` 都放行是刻意的——JSON 反序列化只会给 `list`，但 Python 侧
    调用方（如 console 命令）可能传 tuple，挡掉它属于收口过宽。
    """
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, clear_fields=good_value,
    )

    assert res["success"] is True
    assert res["action"] == "reviewer_updated"
    assert task.reviewer_id is None
    dal.event_dal.append_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_none_and_empty_list_still_mean_not_provided(dal):
    """类型收口不得破坏规则 5：`None` / `[]` 仍是「未提供」，不是「非法」。

    这条是收口的回归底线——收口若写成「只接受 list 且非空」，空数组会被误判成
    `invalid_clear_field`，规则 5 当场破。
    """
    for value in (None, []):
        task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
        dal.task_dal.get_task = AsyncMock(return_value=task)

        res = await taskService.update_task(
            team_id=1, caller_id=6, task_id=1, note="只是评论", clear_fields=value,
        )

        assert res["success"] is True, f"{value!r} 应视为未提供"
        assert res["action"] == "commented"
        assert task.reviewer_id == 20
        dal.task_dal.save_task.assert_not_awaited()
