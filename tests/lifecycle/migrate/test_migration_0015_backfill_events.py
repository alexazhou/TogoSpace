from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0015 = "0015_backfill_events_and_fix_reviewing_tasks.sql"

# 补出来的历史记录正文 = 固定前缀 + 任务原有的内容（2026-10-25 Operator 定的方案 A）
PREFIX = "【系统迁移自动生成】"

# 为什么这些用例不能放进通用测试基类里跑：
# 通用基类（tests/base.py）每次都会删库、再建一个空库并全量迁移，所以任务表里不会有"改造前的老任务"，
# 0015 的"补历史记录"这一步永远没有数据可补，等于没测到。
# 所以这里改成独立用例：先迁移到 0014，手动塞几条老任务，再触发 0015，才能真正验证"给老任务补上历史记录"。


def _setup_before_0015(db_path: Path) -> None:
    """先把数据库迁移到 0014 的状态（已有事件表、已有 reviewer_id 字段），但还没跑 0015。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="15")


def _run_0015(db_path: Path) -> None:
    """跑 0015 这一步迁移（给老任务补历史记录）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")


def _insert_legacy_tasks(db_path: Path) -> None:
    """往任务表里塞几条"改造前的老任务"，让它们的状态、结果、卡住原因各不相同，覆盖各种情况。"""
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
    """把 0015 里"防止重复插入"的那段条件（WHERE NOT EXISTS ...）删掉，用来做反向验证。"""
    pattern = re.compile(
        r"WHERE\s+NOT EXISTS\s*\(\s*SELECT 1 FROM agent_task_events AS e"
        r"\s+WHERE e\.task_id = t\.id\s*\)\s*AND\s+",
        re.IGNORECASE | re.DOTALL,
    )
    mutated, hits = pattern.subn("WHERE ", sql)
    assert hits == 1, f"反向验证自身失效：未能唯一命中防重复条件（命中 {hits} 次）"
    return mutated


def test_0015_backfill_backfills_snapshot_and_content_by_status(tmp_path: Path) -> None:
    """跑完 0015 后，按任务状态给每种老任务补出正确的历史记录：正文取"结果"或"卡住原因"中合适的那个，两者都空则不补。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)

    _run_0015(db_path)

    events = _read_events_by_title(db_path)

    # ① 已完成的任务：历史记录正文取它的 result 字段
    done = _sole_event(events, "t_done")
    assert done["actor_id"] == -2, "历史记录必须标记为系统生成（actor_id=-2）"
    assert done["from_status"] is None, "快照 from_status 必须为 NULL"
    assert done["to_status"] == "DONE", "快照 to_status 应等于任务当前状态"
    assert done["content"] == PREFIX + "done-result"
    assert done["created_at"], "created_at 必须显式写入迁移时刻"

    # ② 已取消的任务：只有"卡住原因"、没有"结果"，正文取卡住原因
    cancelled = _sole_event(events, "t_cancelled")
    assert cancelled["to_status"] == "CANCELLED"
    assert cancelled["content"] == PREFIX + "cancel-block"

    # ③ 暂停的任务：优先取"卡住原因"（哪怕还留着一个旧的结果）
    on_hold = _sole_event(events, "t_on_hold")
    assert on_hold["to_status"] == "ON_HOLD"
    assert on_hold["content"] == PREFIX + "hold-reason"

    # ④ 进行中的任务："结果"和"卡住原因"谁有值取谁（这条是取结果）
    inflight = _sole_event(events, "t_todo_inflight")
    assert inflight["to_status"] == "TODO"
    assert inflight["content"] == PREFIX + "inflight-result"

    # ⑤ 曾被搁置又恢复的任务：留下的"卡住原因"不能丢
    leftover = _sole_event(events, "t_reviewing_leftover")
    assert leftover["to_status"] == "REVIEWING"
    assert leftover["content"] == PREFIX + "leftover-block"

    # ⑤' "结果"和"卡住原因"都有值：默认取结果
    both = _sole_event(events, "t_todo_both")
    assert both["to_status"] == "TODO"
    assert both["content"] == PREFIX + "result-wins"

    # ⑥ 两个都为空：不补历史记录
    assert "t_pending_empty" not in events, "两个都为空的老任务，不应该产生历史记录"


def test_0015_backfill_time_is_local_not_utc(tmp_path: Path) -> None:
    """验证补出来的时间用的是本地时区，而不是数据库默认的 UTC（否则会差好几个小时）。"""
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
    # 只比较到分钟：只要用了本地时区，就该和"本地当前时间"在同一分钟内；若用成 UTC，会差好几个小时
    assert created_at[:16] == str(row[1])[:16], f"created_at={created_at} 非本地时区"


def test_0015_backfill_is_idempotent(tmp_path: Path) -> None:
    """直接手动把 0015 的 SQL 再跑一遍，验证它自己不会多插数据（幂等）。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]

    # 手动把 0015 的 SQL 再跑一遍，看会不会多插数据（迁移框架本身有记账、不会重复跑，所以要手动试）
    sql = (MIGRATIONS_DIR / MIGRATION_0015).read_text(encoding="utf-8")
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(sql)
        after = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]

    assert after == before, "重复执行 0015 应 0 增量（幂等）"


def test_0015_backfill_rerun_after_migration_record_lost(tmp_path: Path) -> None:
    """更可靠的幂等验证：删掉"0015 已经跑过"的记账，让框架真把 0015 再跑一遍，结果应该事件总数不变、每个老任务仍只有一条历史记录。"""
    db_path = tmp_path / "test.db"
    _setup_before_0015(db_path)
    _insert_legacy_tasks(db_path)
    _run_0015(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        before_total = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]
        before_per_task = _event_counts(conn)
        conn.execute("DELETE FROM _migrations WHERE name = ?", (MIGRATION_0015,))
        conn.commit()

    # 记账删掉后，0015 又变成"待执行"，这次才会真的再跑一遍
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")

    with sqlite3.connect(str(db_path)) as conn:
        after_total = conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0]
        after_per_task = _event_counts(conn)

    assert after_total == before_total, "重跑 0015 应 0 增量（SQL 自身幂等）"
    assert after_per_task == before_per_task, "每个无事件老任务仍应恰 1 条快照"


def test_0015_backfill_dedup_guard_negative_control(tmp_path: Path) -> None:
    """反向验证：把"防止重复插入"的条件删掉后跑两遍，必须真的插出重复数据，才能证明前面的幂等断言不是摆设。"""
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

    assert after_second > after_first, "删掉防重复条件后，重复执行应该插出重复数据（否则这个反向验证本身没意义）"


def test_0015_backfill_skips_tasks_that_already_have_events(tmp_path: Path) -> None:
    """已经有历史记录的老任务，不要再补一条（既不能漏、也不能重复）。"""
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
