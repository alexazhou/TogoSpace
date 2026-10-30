from __future__ import annotations

import sqlite3
from pathlib import Path

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0011 = "0011_nullable_room_max_turns.sql"


def _setup_db_before_0011(db_path: Path) -> None:
    """把数据库迁移到 0011 之前的状态（还没执行"把 max_turns 改成可空"的那一步）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="11")


def _insert_rooms(db_path: Path) -> None:
    """往 rooms 表塞三间房，max_turns 分别取 100、50、0，覆盖"要转成 NULL"和"要原样保留"两种情形。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO rooms (team_id, name, type, max_turns, updated_at)"
            " VALUES (?, ?, ?, ?, '')",
            [
                (1, "room_default", "chat", 100),
                (1, "room_custom", "chat", 50),
                (1, "room_zero", "chat", 0),
            ],
        )


def test_0011_converts_100_to_null_and_preserves_others(tmp_path: Path) -> None:
    """跑完 0011 后，原来用 100 表示"不限轮次"的房间要变成 NULL，其他自定义值（50、0）必须原样保留。"""
    db_path = tmp_path / "test.db"
    _setup_db_before_0011(db_path)
    _insert_rooms(db_path)

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="12")

    with sqlite3.connect(str(db_path)) as conn:
        rows = {
            name: max_turns
            for name, max_turns in conn.execute(
                "SELECT name, max_turns FROM rooms ORDER BY name"
            )
        }

    assert rows["room_default"] is None, "max_turns=100 应转为 NULL"
    assert rows["room_custom"] == 50, "自定义值应保留"
    assert rows["room_zero"] == 0, "max_turns=0 应保留"


def test_0011_column_is_nullable_after_migration(tmp_path: Path) -> None:
    """跑完 0011 后，max_turns 列要真的允许写 NULL——能成功插入一条 max_turns 为 NULL 的房间。"""
    db_path = tmp_path / "test.db"
    _setup_db_before_0011(db_path)
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="12")

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO rooms (team_id, name, type, max_turns, updated_at)"
            " VALUES (1, 'room_null', 'chat', NULL, '')"
        )
        row = conn.execute(
            "SELECT max_turns FROM rooms WHERE name = 'room_null'"
        ).fetchone()

    assert row[0] is None
