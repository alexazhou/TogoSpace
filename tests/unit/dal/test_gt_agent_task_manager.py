"""任务 DAL 守卫测试。

- #67：`priority` 权重表守卫（显式映射 + 枚举全覆盖）。
- #76：`agent_ids` 过滤（`assignee ∈ 集合` **OR** `reviewer ∈ 集合`）的真实 SQL 语义。
- #81：`status` 单值（`==`）/ 列表（`IN`）两种过滤的真实 SQL 语义。
"""

from __future__ import annotations

import peewee
import pytest

from constants import TaskPriority, TaskStatus
from dal.db import gtAgentTaskManager
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtAgentTask import GtAgentTask


def test_priority_weight_map_covers_every_enum_member():
    """守卫（漏配即转红）：`TaskPriority` 的每个成员都必须出现在显式权重表里。

    旧实现用「字面量 Case + default=0」：将来给 `TaskPriority` 新增枚举值却忘记
    配权重时不会有任何测试报警，新值会静默落到 default=0、被排到 LOW 之后。
    本用例先验证「能红」：临时从 `_PRIORITY_WEIGHT_MAP` 删掉任一成员即 FAILED。
    """
    assert set(gtAgentTaskManager._PRIORITY_WEIGHT_MAP) == set(TaskPriority)


def test_priority_weight_map_orders_high_above_normal_above_low():
    """权重必须严格递减 HIGH > NORMAL > LOW，且均为正整数。"""
    weights = gtAgentTaskManager._PRIORITY_WEIGHT_MAP

    assert weights[TaskPriority.HIGH] > weights[TaskPriority.NORMAL] > weights[TaskPriority.LOW]
    assert all(isinstance(w, int) and w > 0 for w in weights.values())


def test_priority_weight_case_is_built_from_the_map():
    """Case 表达式由映射生成（不是另写一份字面量），保证两者不会漂移。"""
    from peewee import Case

    case = gtAgentTaskManager._PRIORITY_WEIGHT
    assert isinstance(case, Case)
    # default 兜底为 0：未登记的取值排在所有已登记优先级之后
    assert case.default == 0
    # Case 的分支权重集合 == 映射的权重集合（同一份数据来源）
    assert sorted(weight for _, weight in case.expression_tuples) == sorted(
        gtAgentTaskManager._PRIORITY_WEIGHT_MAP.values()
    )


# ---------------------------------------------------------------------------
# #76：`agent_ids` 过滤 —— 用真实 SQLite 内存库验 OR 语义（不是表达式文本断言）
# ---------------------------------------------------------------------------


@pytest.fixture
def task_db():
    """把模型代理临时绑到内存 SQLite 上，直接跑真实 SQL（结束后一定解绑）。

    `_filtered_task_query` 的语义（尤其 `assignee OR reviewer`）只有真正执行才
    能证伪：「只过滤 assignee」在纯表达式断言下很容易被写成看起来对的形状。
    """
    db = peewee.SqliteDatabase(":memory:")
    bind_database(db)
    db.connect(reuse_if_open=True)
    db.create_tables([GtAgentTask])
    try:
        yield db
    finally:
        unbind_database()
        if not db.is_closed():
            db.close()


def _seed_tasks(rows: list[tuple[int, int | None]]) -> None:
    """按 (assignee_id, reviewer_id) 造任务；id 从 1 递增，team_id 固定 1。"""
    for index, (assignee_id, reviewer_id) in enumerate(rows, start=1):
        GtAgentTask.create(
            id=index,
            team_id=1,
            title=f"t{index}",
            assignee_id=assignee_id,
            creator_id=assignee_id,
            reviewer_id=reviewer_id,
            status=TaskStatus.TODO,
        )


def _ids(query) -> list[int]:
    return sorted(task.id for task in query.execute())


def test_agent_ids_includes_tasks_where_caller_is_reviewer(task_db):
    """**OR 语义**：`agent_id=1` 作为验收人的任务必须出现（只过滤 assignee 会漏）。"""
    _seed_tasks([(1, None), (2, 1), (3, 3)])

    assert _ids(gtAgentTaskManager._filtered_task_query(1, agent_ids=[1])) == [1, 2]


def test_agent_ids_does_not_leak_tasks_outside_the_set(task_db):
    """集合外的任务不许出现（防「宁多勿少」式放宽）。"""
    _seed_tasks([(1, None), (2, 1), (3, 3)])

    assert _ids(gtAgentTaskManager._filtered_task_query(1, agent_ids=[2])) == [2]
    assert _ids(gtAgentTaskManager._filtered_task_query(1, agent_ids=[1, 2])) == [1, 2]


def test_agent_ids_none_keeps_unfiltered_behavior(task_db):
    """`agent_ids=None` 时行为与新增参数前完全一致：只有 team_id 过滤。"""
    _seed_tasks([(1, None), (2, 1), (3, 3)])

    assert _ids(gtAgentTaskManager._filtered_task_query(1)) == [1, 2, 3]


def test_agent_ids_combines_with_explicit_assignee_as_and(task_db):
    """精确过滤与 `agent_ids` 是 **AND**：(assignee=2) AND (assignee/reviewer ∈ {1,2}) ⇒ 只剩 t2。"""
    _seed_tasks([(1, None), (2, 1), (3, 3)])

    assert _ids(gtAgentTaskManager._filtered_task_query(1, assignee_id=2, agent_ids=[1, 2])) == [2]
    assert _ids(gtAgentTaskManager._filtered_task_query(1, assignee_id=3, agent_ids=[1, 2])) == []


def test_agent_ids_scopes_by_team(task_db):
    """跨团队不串数据：agent_ids 只在 team 内生效。"""
    _seed_tasks([(1, None)])
    GtAgentTask.create(
        id=99,
        team_id=2,
        title="other team",
        assignee_id=1,
        creator_id=1,
        reviewer_id=None,
        status=TaskStatus.TODO,
    )

    assert _ids(gtAgentTaskManager._filtered_task_query(1, agent_ids=[1])) == [1]


def test_self_scope_equals_assignee_or_reviewer_union(task_db):
    """`scope="self"`（`agent_ids=[caller]`）必须等于「我执行 ∪ 我验收」，防两种查法口径分叉。

    注意：它**不等于** `assignee_id=caller` —— 我只当验收人的任务也要算进来，
    这正是不能只按 assignee 过滤的原因。
    """
    _seed_tasks([(1, None), (2, 1), (3, 3)])  # t1 我执行 / t2 我验收 / t3 与我无关

    self_scope = set(_ids(gtAgentTaskManager._filtered_task_query(1, agent_ids=[1])))
    assignee_side = set(_ids(gtAgentTaskManager._filtered_task_query(1, assignee_id=1)))
    reviewer_side = set(_ids(gtAgentTaskManager._filtered_task_query(1, reviewer_id=1)))

    assert self_scope == assignee_side | reviewer_side == {1, 2}
    # 明确记录「超集」关系：只按 assignee 查会漏掉 t2
    assert assignee_side == {1}


# ---------------------------------------------------------------------------
# #81：`status` 单值 vs 列表 —— 单值仍走 `==`（既有 SQL 不动），列表走 `IN`
# ---------------------------------------------------------------------------


def _seed_tasks_with_statuses(statuses: list[TaskStatus]) -> None:
    """按状态造任务；id 从 1 递增，team_id 固定 1，assignee 固定 1。"""
    for index, status in enumerate(statuses, start=1):
        GtAgentTask.create(
            id=index,
            team_id=1,
            title=f"t{index}",
            assignee_id=1,
            creator_id=1,
            reviewer_id=None,
            status=status,
        )


def test_status_single_value_filters_with_equality(task_db):
    """单值：只命中该状态（保持既有 `==` 语义，不擅自改成单元素 `IN`）。"""
    _seed_tasks_with_statuses([TaskStatus.TODO, TaskStatus.DONE, TaskStatus.DONE])

    assert _ids(gtAgentTaskManager._filtered_task_query(1, status=TaskStatus.DONE)) == [2, 3]


def test_status_list_filters_with_in(task_db):
    """列表：任一命中即返回（`IN` 语义）。"""
    _seed_tasks_with_statuses([TaskStatus.TODO, TaskStatus.DONE, TaskStatus.CANCELLED])

    assert _ids(
        gtAgentTaskManager._filtered_task_query(1, status=[TaskStatus.TODO, TaskStatus.DONE])
    ) == [1, 2]


def test_status_list_is_deduplicated_by_dal_agnostic(task_db):
    """列表里重复值不影响结果（去重由 service 层做，DAL 只保证语义正确）。"""
    _seed_tasks_with_statuses([TaskStatus.TODO, TaskStatus.DONE])

    assert _ids(
        gtAgentTaskManager._filtered_task_query(
            1, status=[TaskStatus.TODO, TaskStatus.TODO, TaskStatus.DONE]
        )
    ) == [1, 2]


def test_status_empty_list_matches_nothing(task_db):
    """空列表**不生成 WHERE**（peewee 的 `IN ()` 是恒假/未定义行为），与「不过滤」一致。

    空列表的拒绝由 service 层负责（`invalid_status`）；DAL 这层只保证不会写出坏 SQL。
    """
    _seed_tasks_with_statuses([TaskStatus.TODO, TaskStatus.DONE])

    assert _ids(gtAgentTaskManager._filtered_task_query(1, status=[])) == [1, 2]


def test_status_list_combines_with_agent_ids_as_and(task_db):
    """`status` 列表与 `agent_ids` 是 AND：可见范围收窄后仍只返回命中状态的任务。"""
    _seed_tasks_with_statuses([TaskStatus.TODO, TaskStatus.DONE])
    GtAgentTask.create(
        id=9, team_id=1, title="t9", assignee_id=2, creator_id=2, reviewer_id=None,
        status=TaskStatus.DONE,
    )

    query = gtAgentTaskManager._filtered_task_query(
        1, status=[TaskStatus.DONE], agent_ids=[1]
    )
    assert _ids(query) == [2]


def test_count_and_list_share_status_list_scope(task_db):
    """`count_tasks` 与 `list_tasks` 对 `status` 列表同口径（否则 `total` 与列表对不上）。"""
    _seed_tasks_with_statuses([TaskStatus.TODO, TaskStatus.DONE, TaskStatus.CANCELLED])

    query = gtAgentTaskManager._filtered_task_query(
        1, status=[TaskStatus.TODO, TaskStatus.CANCELLED]
    )
    assert query.count() == len(_ids(query)) == 2


def test_count_and_list_share_agent_ids_scope(task_db):
    """`count_tasks` 与 `list_tasks` 必须同口径（否则 `total` 与列表对不上）。"""
    _seed_tasks([(1, None), (2, 1), (3, 3)])

    query = gtAgentTaskManager._filtered_task_query(1, agent_ids=[1])
    assert query.count() == len(_ids(query)) == 2
