from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0015 = "0015_backfill_events_and_fix_reviewing_tasks.sql"

# 为什么这些用例不能放进通用测试基类里跑：
# 通用基类（tests/base.py）每次都会删库、再建一个空库并全量迁移，所以任务表里不会有"改造前的老任务"，
# 0015 的"修状态错乱的老任务"这一步永远没有数据可修，等于没测到。
# 所以这里改成独立用例：先迁移到 0014，手动塞几条老任务，再触发 0015，才能真正验证"修好那些验收人为空的任务"。

# 造的老任务，字段依次是：标题、状态、验收人、创建人。
# 创建人一定非空（表结构要求），所以每行都给它填 8。
LEGACY_ROWS = [
    # 应该被修：状态是"待验收"、但验收人是空的，把创建人填成验收人
    ("t_reviewing_no_reviewer", "REVIEWING", None, 8),
    # 不该动：待验收且已经有验收人了（哪怕验收人和创建人不是同一个人）
    ("t_reviewing_has_reviewer", "REVIEWING", 6, 8),
    # 不该动：状态不是"待验收"
    ("t_todo_no_reviewer", "TODO", None, 8),
    ("t_on_hold_no_reviewer", "ON_HOLD", None, 8),
    # 不该动：已经结束的状态（完成 / 取消）
    ("t_done_no_reviewer", "DONE", None, 8),
    ("t_cancelled_no_reviewer", "CANCELLED", None, 8),
]
# 期望修好之后，验收人应该变成谁（按标题取）
EXPECTED_FIXED = {
    "t_reviewing_no_reviewer": 8,
}


def _setup_before_0015(db_path: Path) -> None:
    """先把数据库迁移到 0014 的状态（已有事件表、已有 reviewer_id 字段），但还没跑 0015。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="15")


def _run_0015(db_path: Path) -> None:
    """跑 0015 这一步迁移（修验收人为空的老任务）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")


def _insert_legacy_tasks(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO agent_tasks"
            " (team_id, title, description, assignee_id, creator_id, reviewer_id,"
            "  status, priority, parent_id, depends_on, room_id)"
            " VALUES (1, ?, '', 8, ?, ?, ?, 'NORMAL', NULL, '[]', NULL)",
            [(title, creator, reviewer, status) for title, status, reviewer, creator in LEGACY_ROWS],
        )


def _reviewer_by_title(db_path: Path) -> dict[str, int | None]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT title, reviewer_id FROM agent_tasks").fetchall()
    return {str(title): (None if reviewer is None else int(reviewer)) for title, reviewer in rows}


def _strip_null_condition(sql: str) -> str:
    """把 0015 里"只在验收人为空时处理"的条件（AND reviewer_id IS NULL）删掉，用来做反向验证。"""
    pattern = re.compile(r"\n\s+AND\s+reviewer_id\s+IS\s+NULL", re.IGNORECASE)
    mutated, hits = pattern.subn("", sql)
    assert hits == 1, f"反向验证自身失效：未能唯一命中 reviewer_id IS NULL 条件（命中 {hits} 次）"
    return mutated


def test_0015_fix_fixes_reviewing_without_reviewer(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    _run_0015(db_path)

    reviewers = _reviewer_by_title(db_path)

    # ① 应该被修：待验收且验收人为空，把创建人填成验收人
    for title, expected in EXPECTED_FIXED.items():
        assert reviewers[title] == expected, f"{title} 应订正为 reviewer_id={expected}，实际 {reviewers[title]}"

    # ② 不该动：待验收但已经有验收人的，保持原样
    assert reviewers["t_reviewing_has_reviewer"] == 6, "已经待验收且已有验收人的任务，不得被覆盖"

    # ③ 不该动：状态不是"待验收"的（含已完成/取消），验收人保持为空
    assert reviewers["t_todo_no_reviewer"] is None
    assert reviewers["t_on_hold_no_reviewer"] is None
    assert reviewers["t_done_no_reviewer"] is None
    assert reviewers["t_cancelled_no_reviewer"] is None


def test_0015_fix_is_idempotent(tmp_path: Path) -> None:
    """直接手动把 0015 的 SQL 再跑一遍，验证它自己不会再改任何行（幂等）。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    rows_after_first = _reviewer_by_title(db_path)

    sql = (MIGRATIONS_DIR / MIGRATION_0015).read_text(encoding="utf-8")
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(sql)
        changed = conn.total_changes
        conn.executescript(sql)

    assert changed == 0, f"重复执行 0015 应 0 增量（幂等），实际变更 {changed} 行"
    assert _reviewer_by_title(db_path) == rows_after_first, "重复执行不得再改任何行"


def test_0015_fix_rerun_after_migration_record_lost(tmp_path: Path) -> None:
    """验证幂等：删掉"0015 已经跑过"的记账再重跑一遍，结果应该完全不变。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    before = _reviewer_by_title(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM _migrations WHERE name = ?", (MIGRATION_0015,))
        conn.commit()

    # 记账删掉后，0015 又变成"待执行"，这次才会真的再跑一遍
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")

    assert _reviewer_by_title(db_path) == before, "重跑 0015 应 0 增量（SQL 自身幂等）"


def test_0015_fix_null_guard_negative_control(tmp_path: Path) -> None:
    """反向验证：删掉"只在验收人为空时处理"的条件，就会连"已经有验收人的任务"一起改错，证明前面的范围断言不是摆设。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    sql = (MIGRATIONS_DIR / MIGRATION_0015).read_text(encoding="utf-8")
    mutated = _strip_null_condition(sql)

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(mutated)

    reviewers = _reviewer_by_title(db_path)

    # 删掉条件后：那条"待验收但已有验收人"的任务，被错误地改成了创建人 8
    assert reviewers["t_reviewing_has_reviewer"] == 8, (
        "删掉「只在验收人为空时处理」的条件后，应该会误改已有验收人的任务（否则反向验证本身没意义）"
    )
    # 正向仍然成立：原本就该修的那条，还是被修对了
    assert reviewers["t_reviewing_no_reviewer"] == 8
