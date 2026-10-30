from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0015 = "0015_backfill_events_and_fix_reviewing_tasks.sql"

# 0015（历史结转部分）快照 content = 固定前缀 + 逐档原文（Operator 2026-10-25 裁定 (A)）
PREFIX = "【系统迁移自动生成】"

# ⚠️ 专用用例说明：tests/base.py 每次 `cleanup_sqlite_files()`（删库）+ `prepare_sqlite_schema()`
# （空库全量迁移）⇒ 通用测试链路里 agent_tasks 无存量数据、迁移 0015（历史结转部分）恒 0 行、验不了结转。
# 因此必须用本文件这种「up_to 分段：先跑到 0014 造存量 → 再触发 0015（历史结转部分）」的独立用例。


def _setup_before_0015(db_path: Path) -> None:
    """跑到 0014（合并：建 agent_task_events + 改名 manager_id→reviewer_id），再触发 0015 的历史结转部分。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="15")


def _run_0015(db_path: Path) -> None:
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")


def _insert_legacy_tasks(db_path: Path) -> None:
    """造 v2 之前的存量任务（status / result / block_reason 各异）。"""
    rows = [
        # (title, status, result, block_reason)
        ("t_done", "DONE", "done-result", ""),
        ("t_cancelled", "CANCELLED", "", "cancel-block"),
        ("t_on_hold", "ON_HOLD", "stale-result", "hold-reason"),
        ("t_todo_inflight", "TODO", "inflight-result", ""),
        ("t_todo_both", "TODO", "result-wins", "block-loses"),
        ("t_reviewing_leftover", "REVIEWING", "", "leftover-block"),
        ("t_pending_empty", "PENDING", "", ""),
    ]
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO agent_tasks"
            " (team_id, title, description, assignee_id, creator_id, reviewer_id,"
            "  status, priority, parent_id, depends_on, room_id, result, block_reason)"
            " VALUES (1, ?, '', 8, 8, 6, ?, 'NORMAL', NULL, '[]', NULL, ?, ?)",
            rows,
        )


def _task_id(db_path: Path, title: str) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT id FROM agent_tasks WHERE title = ?", (title,)).fetchone()
    assert row is not None, f"测试数据缺失：{title}"
    return int(row[0])


def _insert_preexisting_event(db_path: Path, task_id: int, content: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agent_task_events"
            " (task_id, actor_id, from_status, to_status, content, created_at, updated_at)"
            " VALUES (?, 8, 'TODO', 'IN_PROGRESS', ?,"
            "  datetime('now', 'localtime'), datetime('now', 'localtime'))",
            (task_id, content),
        )


def _read_events_by_title(db_path: Path) -> dict[str, list[dict]]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT t.title AS title, e.actor_id AS actor_id, e.from_status AS from_status,"
            " e.to_status AS to_status, e.content AS content, e.created_at AS created_at"
            " FROM agent_task_events AS e JOIN agent_tasks AS t ON t.id = e.task_id"
            " ORDER BY t.title, e.id"
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["title"], []).append(dict(row))
    return out


def _sole_event(events: dict[str, list[dict]], title: str) -> dict:
    rows = events.get(title, [])
    assert len(rows) == 1, f"{title} 应恰有 1 条快照事件，实际 {len(rows)} 条"
    return rows[0]


def _event_counts(conn: sqlite3.Connection) -> dict[int, int]:
    return {
        int(task_id): int(cnt)
        for task_id, cnt in conn.execute(
            "SELECT task_id, COUNT(*) FROM agent_task_events GROUP BY task_id"
        )
    }


def _strip_dedup_guard(sql: str) -> str:
    """摘掉 0015（历史结转部分）的判重守卫（`WHERE NOT EXISTS (...) AND `），用于负对照。"""
    pattern = re.compile(
        r"WHERE\s+NOT EXISTS\s*\(\s*SELECT 1 FROM agent_task_events AS e"
        r"\s+WHERE e\.task_id = t\.id\s*\)\s*AND\s+",
        re.IGNORECASE | re.DOTALL,
    )
    mutated, hits = pattern.subn("WHERE ", sql)
    assert hits == 1, f"负对照自身失效：未能唯一命中判重守卫（命中 {hits} 次）"
    return mutated


def test_0015_backfill_backfills_snapshot_and_content_by_status(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    _run_0015(db_path)

    events = _read_events_by_title(db_path)

    # ① 终态 → result
    done = _sole_event(events, "t_done")
    assert done["actor_id"] == -2, "快照必须显式署名系统（actor_id=-2）"
    assert done["from_status"] is None, "快照 from_status 必须为 NULL"
    assert done["to_status"] == "DONE", "快照 to_status 应等于任务当前状态"
    assert done["content"] == PREFIX + "done-result"
    assert done["created_at"], "created_at 必须显式写入迁移时刻"

    # ② CANCELLED 有 block_reason、无 result → 回退 block_reason
    cancelled = _sole_event(events, "t_cancelled")
    assert cancelled["to_status"] == "CANCELLED"
    assert cancelled["content"] == PREFIX + "cancel-block"

    # ③ ON_HOLD → 取 block_reason（优先于残留 result）
    on_hold = _sole_event(events, "t_on_hold")
    assert on_hold["to_status"] == "ON_HOLD"
    assert on_hold["content"] == PREFIX + "hold-reason"

    # ④ 在途 TODO → 取非空那一列（result）
    inflight = _sole_event(events, "t_todo_inflight")
    assert inflight["to_status"] == "TODO"
    assert inflight["content"] == PREFIX + "inflight-result"

    # ⑤ 曾搁置后恢复的在途任务 → 残留 block_reason 不丢
    leftover = _sole_event(events, "t_reviewing_leftover")
    assert leftover["to_status"] == "REVIEWING"
    assert leftover["content"] == PREFIX + "leftover-block"

    # ⑤' 在途且两列都非空 → 默认 result 优先
    both = _sole_event(events, "t_todo_both")
    assert both["to_status"] == "TODO"
    assert both["content"] == PREFIX + "result-wins"

    # ⑥ 两列皆空 → 不插快照
    assert "t_pending_empty" not in events, "两列皆空的老任务不应产生快照"


def test_0015_backfill_time_is_local_not_utc(tmp_path: Path) -> None:
    """created_at 必须显式取本地时区，不得依赖 DDL 默认（默认日期时间('now')=UTC）。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    _run_0015(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT e.created_at AS created_at, datetime('now', 'localtime') AS local_now,"
            " datetime('now') AS utc_now"
            " FROM agent_task_events AS e ORDER BY e.id LIMIT 1"
        ).fetchone()
    assert row is not None
    created_at = str(row[0])
    # 与「本地时区当前时刻」同分钟（同表 UTC/本地两种时区会导致差异 ≥ 时区偏移，通常数小时）
    assert created_at[:16] == str(row[1])[:16], f"created_at={created_at} 非本地时区"


def test_0015_backfill_is_idempotent(tmp_path: Path) -> None:
    """直跑 0015 SQL 两次（绕开 runner 记账），验证 SQL 自身幂等。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]

    # 直接重放 0015 的 SQL（迁移框架已把 0015 记为 applied、不会重跑），验证脚本自身幂等
    sql = (MIGRATIONS_DIR / MIGRATION_0015).read_text(encoding="utf-8")
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(sql)
        after = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]

    assert after == before, "重复执行 0015 应 0 增量（幂等）"


def test_0015_backfill_rerun_after_migration_record_lost(tmp_path: Path) -> None:
    """真验幂等（推荐路径 A）：删掉 `_migrations` 里的 0015 行后重跑，事件表 0 增量、每单仍恰 1 条。

    注：重复调 `migrate_database` 因 `_migrations` 已记账而是 no-op（`src/db.py:100-103/133/136`）
    ⇒「重复执行为 0 增量」是空断言；必须删记账或直跑 SQL 才能真验。
    """
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        before_total = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]
        before_per_task = _event_counts(conn)
        conn.execute("DELETE FROM _migrations WHERE name = ?", (MIGRATION_0015,))
        conn.commit()

    # 记账已丢 ⇒ 0015 重新成为 pending、会被真正再次执行
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")

    with sqlite3.connect(str(db_path)) as conn:
        after_total = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]
        after_per_task = _event_counts(conn)

    assert after_total == before_total, "重跑 0015 应 0 增量（SQL 自身幂等）"
    assert after_per_task == before_per_task, "每个无事件老任务仍应恰 1 条快照"


def test_0015_backfill_dedup_guard_negative_control(tmp_path: Path) -> None:
    """负对照：摘掉 `WHERE NOT EXISTS` 判重守卫后重跑两次，必须产生重复行 ⇒ 证明幂等断言能红。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    sql = (MIGRATIONS_DIR / MIGRATION_0015).read_text(encoding="utf-8")
    mutated = _strip_dedup_guard(sql)

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(mutated)
        after_first = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]
        conn.executescript(mutated)
        after_second = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]

    assert after_second > after_first, "摘掉判重守卫后重复执行应产生重复行（否则负对照自身失效）"


def test_0015_backfill_skips_tasks_that_already_have_events(tmp_path: Path) -> None:
    """混合库：已有事件的老任务不再补快照（不漏、不重）。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    done_id = _task_id(db_path, "t_done")
    _insert_preexisting_event(db_path, done_id, "preexisting")

    _run_0015(db_path)

    events = _read_events_by_title(db_path)
    assert len(events["t_done"]) == 1, "已有事件的老任务不应再补快照"
    assert events["t_done"][0]["content"] == "preexisting"
    # 其余无事件的老任务照常补
    assert _sole_event(events, "t_todo_inflight")["content"] == PREFIX + "inflight-result"
