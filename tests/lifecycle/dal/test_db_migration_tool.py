from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import db


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """读出某张表的全部列名，方便断言"该有的列在、不该有的列不在"。"""
    rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return {str(row[1]) for row in rows}


def test_migrate_database_applies_all_pending_and_is_idempotent(tmp_path: Path) -> None:
    """迁移工具要把所有待执行的迁移一次跑完、再跑一遍什么都不做，顺带抽查几张表的结构是否符合最新版本。"""
    db_path = tmp_path / "data.db"

    first_applied = db.migrate_database(db_path)
    second_applied = db.migrate_database(db_path)

    assert first_applied
    assert second_applied == []

    applied, available = db.migration_status(db_path)
    applied_names = [item.name for item in applied]
    assert applied_names == available

    conn = sqlite3.connect(db_path)
    try:
        assert {"name", "soul", "type"} <= _columns(conn, "role_templates")
        assert "allowed_tools" not in _columns(conn, "role_templates")
        assert "allow_tools" in _columns(conn, "agents")
        assert "driver" not in _columns(conn, "role_templates")
        agent_columns = _columns(conn, "agents")
        assert {"role_template_id"} <= agent_columns
        assert "role_template_name" not in agent_columns
        assert {"config"} <= _columns(conn, "teams")
        assert "max_function_calls" not in _columns(conn, "teams")
        history_columns = _columns(conn, "agent_histories")
        assert {"role", "tool_call_id", "message", "usage"} <= history_columns
        assert "message_json" not in history_columns
        assert "usage_json" not in history_columns
        assert "stage" not in history_columns
        assert "success" not in history_columns
    finally:
        conn.close()


def test_migrate_database_verbose_logs_one_line_per_migration(tmp_path: Path, caplog) -> None:
    """verbose=True 时每个待执行迁移输出一行；默认（库调用）保持安静。"""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_demo.sql").write_text("CREATE TABLE demo (id INTEGER);", encoding="utf-8")
    (migrations_dir / "0002_add_demo_name.sql").write_text(
        "ALTER TABLE demo ADD COLUMN name TEXT;", encoding="utf-8"
    )

    with caplog.at_level(logging.INFO, logger="db"):
        applied = db.migrate_database(tmp_path / "data.db", migrations_dir=migrations_dir, verbose=True)
        quiet_applied = db.migrate_database(tmp_path / "quiet.db", migrations_dir=migrations_dir)

    assert applied == ["0001_create_demo.sql", "0002_add_demo_name.sql"]
    assert quiet_applied == applied
    migration_logs = [record.message for record in caplog.records if record.name == "db"]
    assert migration_logs == [
        "数据库迁移检查: 已执行=0/2, 当前版本=无, 待执行=2",
        "Applying migration: 0001_create_demo.sql",
        "Applying migration: 0002_add_demo_name.sql",
        "数据库迁移完成: 本次执行=2, 当前版本=0002_add_demo_name.sql",
    ]


def test_migrate_database_verbose_logs_summary_when_nothing_pending(tmp_path: Path, caplog) -> None:
    """没有待执行迁移时也要输出一行当前状态，启动日志才能看出迁移检查过。"""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_demo.sql").write_text("CREATE TABLE demo (id INTEGER);", encoding="utf-8")
    database_path = tmp_path / "data.db"
    db.migrate_database(database_path, migrations_dir=migrations_dir)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="db"):
        applied = db.migrate_database(database_path, migrations_dir=migrations_dir, verbose=True)

    assert applied == []
    migration_logs = [record.message for record in caplog.records if record.name == "db"]
    assert migration_logs == [
        "数据库迁移检查: 已执行=1/1, 当前版本=0001_create_demo.sql, 待执行=0",
    ]


def test_has_pending_migrations_reflects_ledger(tmp_path: Path) -> None:
    """待执行判断跟随迁移账本：未执行过为 True，执行完为 False。"""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_demo.sql").write_text("CREATE TABLE demo (id INTEGER);", encoding="utf-8")
    database_path = tmp_path / "data.db"

    assert db.has_pending_migrations(database_path, migrations_dir=migrations_dir) is True

    db.migrate_database(database_path, migrations_dir=migrations_dir)

    assert db.has_pending_migrations(database_path, migrations_dir=migrations_dir) is False


def test_migrate_database_up_to_normalizes_unpadded_number(tmp_path: Path) -> None:
    """up_to 传 "11" 和 "0011" 必须得到完全相同的结果（迁移编号允许带前导零，要先归一化再比较）。"""
    db_path = tmp_path / "data.db"
    # "11" and "0011" should produce identical results
    applied_short = db.migrate_database(tmp_path / "a.db", up_to="11")
    applied_padded = db.migrate_database(tmp_path / "b.db", up_to="0011")
    assert applied_short == applied_padded
