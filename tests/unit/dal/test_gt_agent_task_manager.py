"""#67：任务 DAL 的 priority 权重表守卫（显式映射 + 枚举全覆盖）。"""

from __future__ import annotations

from constants import TaskPriority
from dal.db import gtAgentTaskManager


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
