from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0015 = "0015_backfill_events_and_fix_reviewing_tasks.sql"

# ⚠️ 专用用例说明：tests/base.py 每次 `cleanup_sqlite_files()`（删库）+ `prepare_sqlite_schema()`
# （空库全量迁移）⇒ 通用测试链路里 agent_tasks 无存量数据、迁移 0015（数据订正部分）恒 0 行、验不了订正。
# 因此必须用本文件这种「up_to 分段：先跑到 0014 造存量 → 再触发 0015（数据订正部分）」的独立用例。

# 存量任务造数（title, status, reviewer_id, creator_id）
#   creator_id 恒非空（0009 schema：creator_id INTEGER NOT NULL）。
LEGACY_ROWS = [
    # 应被订正：REVIEWING 且 reviewer 为空 ⇒ reviewer_id := creator_id
    ("t_reviewing_no_reviewer", "REVIEWING", None, 8),
    # 不应动：REVIEWING 已有 reviewer（即便 creator 不同）
    ("t_reviewing_has_reviewer", "REVIEWING", 6, 8),
    # 不应动：非 REVIEWING
    ("t_todo_no_reviewer", "TODO", None, 8),
    ("t_on_hold_no_reviewer", "ON_HOLD", None, 8),
    # 不应动：终态
    ("t_done_no_reviewer", "DONE", None, 8),
    ("t_cancelled_no_reviewer", "CANCELLED", None, 8),
]
# 期望订正后的 reviewer_id（应按 title 取值）
EXPECTED_FIXED = {
    "t_reviewing_no_reviewer": 8,
}


def _setup_before_0015(db_path: Path) -> None:
    """跑到 0014（合并：建 agent_task_events + 改名 manager_id→reviewer_id），再触发 0015（数据订正部分）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="15")


def _run_0015(db_path: Path) -> None:
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
    """摘掉 0015（数据订正部分）的 `AND reviewer_id IS NULL` 条件，用于负对照。"""
    pattern = re.compile(r"\n\s+AND\s+reviewer_id\s+IS\s+NULL", re.IGNORECASE)
    mutated, hits = pattern.subn("", sql)
    assert hits == 1, f"负对照自身失效：未能唯一命中 reviewer_id IS NULL 条件（命中 {hits} 次）"
    return mutated


def test_0015_fix_fixes_reviewing_without_reviewer(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    _run_0015(db_path)

    reviewers = _reviewer_by_title(db_path)

    # ① 应订正：REVIEWING 且 reviewer 为空 ⇒ reviewer_id 置为 creator_id
    for title, expected in EXPECTED_FIXED.items():
        assert reviewers[title] == expected, f"{title} 应订正为 reviewer_id={expected}，实际 {reviewers[title]}"

    # ② 不应动：REVIEWING 已有 reviewer 保持原值
    assert reviewers["t_reviewing_has_reviewer"] == 6, "已验收的老任务不得被覆盖"

    # ③ 不应动：非 REVIEWING / 终态
    assert reviewers["t_todo_no_reviewer"] is None
    assert reviewers["t_on_hold_no_reviewer"] is None
    assert reviewers["t_done_no_reviewer"] is None
    assert reviewers["t_cancelled_no_reviewer"] is None


def test_0015_fix_is_idempotent(tmp_path: Path) -> None:
    """直跑 0015 SQL 两次（绕开 runner 记账），验证 SQL 自身幂等。"""
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
    """真验幂等：删掉 `_migrations` 里 0015 行后重跑，0 增量、结果稳定。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    before = _reviewer_by_title(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM _migrations WHERE name = ?", (MIGRATION_0015,))
        conn.commit()

    # 记账已丢 ⇒ 0015 重新成为 pending、会被真正再次执行
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")

    assert _reviewer_by_title(db_path) == before, "重跑 0015 应 0 增量（SQL 自身幂等）"


def test_0015_fix_null_guard_negative_control(tmp_path: Path) -> None:
    """负对照：摘掉 `reviewer_id IS NULL` 条件 ⇒ 会误改已验收任务 ⇒ 证明订正范围断言能红。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    sql = (MIGRATIONS_DIR / MIGRATION_0015).read_text(encoding="utf-8")
    mutated = _strip_null_condition(sql)

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(mutated)

    reviewers = _reviewer_by_title(db_path)

    # 变异后：已验收任务（reviewer=6、creator=8）被错误覆盖成 creator=8
    assert reviewers["t_reviewing_has_reviewer"] == 8, (
        "摘掉 NULL 守卫后应误改已验收任务（否则负对照自身失效）"
    )
    # 正对照仍成立：原本该订正的行仍被订正
    assert reviewers["t_reviewing_no_reviewer"] == 8
