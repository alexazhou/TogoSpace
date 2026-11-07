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
async def test_terminal_task_clear_reviewer_is_immutable(dal, terminal):
    task = _build_task(status=terminal, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_reviewer=True)

    assert res["success"] is False
    assert res["error_code"] == "invalid_transition"
    assert task.reviewer_id == 20


# ---------------------------------------------------------------------------
# R4｜REVIEWING 时撤销被拒（D2）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_reviewer_rejected_in_reviewing(dal):
    """REVIEWING 时撤销验收人 ⇒ 拒绝，任务仍是「有验收人」状态（不制造无人验收的死锁）。"""
    task = _build_task(status=TaskStatus.REVIEWING, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_reviewer=True)

    assert res["success"] is False
    assert res["error_code"] == "reviewer_required_in_reviewing"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_reviewer_by_non_creator_is_denied(dal):
    """撤销只能由创建人做；当前验收人只能交接，不能撤销（撤销后执行人可直接 DONE）。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=20, task_id=1, clear_reviewer=True)

    assert res["success"] is False
    assert res["error_code"] == "permission_denied"
    assert task.reviewer_id == 20


@pytest.mark.asyncio
async def test_clear_reviewer_by_creator_succeeds_and_warns_consequence(dal):
    """创建人撤销验收人 ⇒ 成功；返回 message 必须明写「执行人可直接置 DONE」的后果。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_agents_by_ids = AsyncMock(return_value=[_agent(20, "测试员甲")])

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_reviewer=True)

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
    """`reviewer_id` 与 `clear_reviewer=True` 同时给 ⇒ ambiguous_reviewer_change，且不改库。"""
    task = _build_task(status=TaskStatus.IN_PROGRESS, assignee_id=11, creator_id=6, reviewer_id=20)
    dal.task_dal.get_task = AsyncMock(return_value=task)
    dal.agent_dal.get_team_agents_by_ids = AsyncMock(return_value=[_agent(21, "测试员乙")])

    res = await taskService.update_task(
        team_id=1, caller_id=6, task_id=1, reviewer_id=21, clear_reviewer=True,
    )

    assert res["success"] is False
    assert res["error_code"] == "ambiguous_reviewer_change"
    assert task.reviewer_id == 20
    dal.task_dal.save_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_reviewer_when_already_none_is_unchanged(dal):
    """本来就没验收人时撤销 ⇒ reviewer_unchanged，不写事件（不产出无意义时间线）。"""
    task = _build_task(status=TaskStatus.TODO, assignee_id=11, creator_id=6, reviewer_id=None)
    dal.task_dal.get_task = AsyncMock(return_value=task)

    res = await taskService.update_task(team_id=1, caller_id=6, task_id=1, clear_reviewer=True)

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
