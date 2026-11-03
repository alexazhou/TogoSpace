"""0019 迁移（补齐 room_messages.seq 存量 NULL）的生命周期测试。

本迁移只做一件事：把存量里 `seq IS NULL` 的消息补成非空 seq，
为「seq 插入即分配、不再用 NULL 表示待注入」这一语义变更清场。

覆盖场景（对应技术方案 §6 的前三种）：
1. 房间内全是 `seq IS NULL`：按 `insert_immediately DESC, id ASC` 从 0 起号；
2. `NULL` 与非空混合：新号从 `MAX(seq)+1` 开始，不覆盖已有号；
3. 存在历史升级留下的 `seq` 空洞：必须用 `MAX(seq)+1` 而不是 `COUNT`，否则撞号；
4. 幂等：重复执行不影响任何数据；
5. 自带显式 `BEGIN; … COMMIT;`（与 0018 同一约定，理由见该迁移文件头部注释）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0019 = "0019_backfill_room_message_seq.sql"


def _migrate_to_0018(db_path: Path) -> None:
    """把数据库迁移到 0018 为止（up_to 是前缀开区间，19 表示 < 0019）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="19")


def _seed(db_path: Path, rows: list[tuple[int, int | None, int]]) -> None:
    """插入 (room_id, seq, insert_immediately) 三元组。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO room_messages (room_id, sender_id, content, send_time,"
            " seq, insert_immediately, created_at, updated_at)"
            " VALUES (?, 1, 'msg', '2026-01-01 00:00:00', ?, ?, '', '')",
            rows,
        )


def _rows(db_path: Path, room_id: int) -> list[tuple[int, int, int]]:
    """按 (seq, id) 返回该房间的 (id, seq, insert_immediately)。"""
    with sqlite3.connect(str(db_path)) as conn:
        return [
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT id, seq, insert_immediately FROM room_messages"
                " WHERE room_id = ? ORDER BY seq, id",
                (room_id,),
            )
        ]


def _null_count(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM room_messages WHERE seq IS NULL").fetchone()[0]


def test_0019_backfills_all_null_room_from_zero(tmp_path: Path) -> None:
    """房间内全是 seq=NULL：按 insert_immediately DESC, id ASC 从 0 起号。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0018(db_path)
    _seed(db_path, [
        (1, None, 0),  # id=1 普通
        (1, None, 1),  # id=2 立即注入 → 应排最前
        (1, None, 0),  # id=3 普通
    ])

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="20")

    assert applied == [MIGRATION_0019]
    assert _null_count(db_path) == 0, "迁移后不应残留 seq IS NULL"
    assert _rows(db_path, 1) == [
        (2, 0, 1),  # immediate 优先
        (1, 1, 0),
        (3, 2, 0),
    ]


def test_0019_mixed_null_and_non_null_starts_after_max(tmp_path: Path) -> None:
    """NULL 与非空混合：新号从 MAX(seq)+1 开始，不动已有号。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0018(db_path)
    _seed(db_path, [
        (1, 0, 0),
        (1, 1, 0),
        (1, None, 1),
        (1, None, 0),
    ])

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="20")

    assert _null_count(db_path) == 0
    assert _rows(db_path, 1) == [
        (1, 0, 0),
        (2, 1, 0),
        (3, 2, 1),
        (4, 3, 0),
    ], "已有序号必须原样保留，补齐的号必须紧接在最大值之后"


def test_0019_avoids_collision_on_historical_gap(tmp_path: Path) -> None:
    """历史升级留下的 seq 空洞：用 MAX(seq)+1 而非 COUNT，否则撞号。

    构造：已有 0,1,3（2 因旧版「升级时清空 seq」被移走后由后续消息占了 3），
    另有两条 NULL 待补。
    - `MAX(seq)+1` ⇒ 从 4 起号，得到 4,5，无撞号 ✅
    - `COUNT(*)`（错误实现）⇒ 该房间共 5 条 ⇒ 从 5 起号…但如果换一种计数方式
      拿到 3，就会写出 3，与已有 3 撞号。
    这条用例把「用 COUNT 会撞号」钉死：断言补出来的号必须严格大于已有最大值。
    """
    db_path = tmp_path / "test.db"
    _migrate_to_0018(db_path)
    _seed(db_path, [
        (1, 0, 0),
        (1, 1, 0),
        (1, 3, 0),   # 2 是空洞
        (1, None, 1),
        (1, None, 0),
    ])

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="20")

    rows = _rows(db_path, 1)
    seqs = [row[1] for row in rows]
    assert _null_count(db_path) == 0
    assert len(seqs) == len(set(seqs)), f"出现重复 seq：{seqs}"
    assert min(seqs[3:]) > 3, f"补齐的号必须严格大于已有最大值 3，实际 {seqs}"
    assert seqs == [0, 1, 3, 4, 5]


def test_0019_leaves_other_rooms_untouched(tmp_path: Path) -> None:
    """多房间互不干扰：已补齐的房间不应被改动，各房间各自独立起号。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0018(db_path)
    _seed(db_path, [
        (1, None, 0),
        (2, 0, 0),
        (2, 1, 0),
        (3, None, 0),
    ])

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="20")

    assert _rows(db_path, 1) == [(1, 0, 0)]
    assert _rows(db_path, 2) == [(2, 0, 0), (3, 1, 0)], "已有完整 seq 的房间不应被改号"
    assert _rows(db_path, 3) == [(4, 0, 0)], "不同房间各自从自己的 MAX(seq)+1 起号"


def test_0019_is_idempotent(tmp_path: Path) -> None:
    """重复执行不改变任何数据（WHERE seq IS NULL 天然幂等）。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0018(db_path)
    _seed(db_path, [(1, None, 1), (1, None, 0), (1, 5, 0)])

    first = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="20")
    before = _rows(db_path, 1)

    # 直接重跑 SQL（绕过账本），模拟「迁移被重复执行」
    sql = (MIGRATIONS_DIR / MIGRATION_0019).read_text(encoding="utf-8")
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(sql)
    after = _rows(db_path, 1)

    assert first == [MIGRATION_0019]
    assert after == before, "重复执行不得改动已补齐的数据"


def test_0019_declares_explicit_transaction() -> None:
    """迁移必须自带显式 `BEGIN; … COMMIT;`（与 0018 同一约定）。"""
    statements = _statement_lines()
    assert statements.count("BEGIN;") == 1, (
        f"迁移必须恰好有一条 BEGIN; 语句，实际 {statements.count('BEGIN;')} 条"
    )
    assert statements.count("COMMIT;") == 1, (
        f"迁移必须恰好有一条 COMMIT; 语句，实际 {statements.count('COMMIT;')} 条"
    )
    assert statements.index("BEGIN;") == 0, "BEGIN; 必须是第一条语句"
    assert statements.index("COMMIT;") == len(statements) - 1, "COMMIT; 必须是最后一条语句"


def test_0019_keeps_seq_column_nullable(tmp_path: Path) -> None:
    """seq 列保持 nullable：不重建表收紧 NOT NULL（技术方案 §2.1 明确要求）。

    这条守的是「不要自作主张把 DB 列改 NOT NULL」——SQLite 收紧 NOT NULL 需重建表
    并搬数据，一旦中断库就停在半迁移状态，风险远大于收益。
    """
    db_path = tmp_path / "test.db"
    _migrate_to_0018(db_path)
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="20")

    with sqlite3.connect(str(db_path)) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(room_messages)")}

    assert "seq" in columns, "seq 列必须存在"
    # row = (cid, name, type, notnull, dflt_value, pk)
    assert columns["seq"][3] == 0, "seq 列必须保持 nullable（notnull=0），不得改成 NOT NULL"


def _strip_sql_comments(text: str) -> str:
    """去掉 SQL 里的 `--` 行注释与 `/* … */` 块注释，不碰单引号字符串内的内容。"""
    out: list[str] = []
    in_quote = False
    in_block = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_block:
            if text[i:i + 2] == "*/":
                in_block = False
                i += 2
                continue
            if ch == "\n":
                out.append(ch)
            i += 1
            continue
        if ch == "'":
            if in_quote and i + 1 < len(text) and text[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_quote = not in_quote
            out.append(ch)
        elif not in_quote and text[i:i + 2] == "--":
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        elif not in_quote and text[i:i + 2] == "/*":
            in_block = True
            i += 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _statement_lines() -> list[str]:
    raw = (MIGRATIONS_DIR / MIGRATION_0019).read_text(encoding="utf-8")
    stripped = _strip_sql_comments(raw)
    return [s for s in (ln.strip() for ln in stripped.splitlines()) if s]
