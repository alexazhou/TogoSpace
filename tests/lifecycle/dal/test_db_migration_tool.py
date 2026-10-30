from __future__ import annotations

import sqlite3
from pathlib import Path

import db


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return {str(row[1]) for row in rows}


def test_migrate_database_applies_all_pending_and_is_idempotent(tmp_path: Path) -> None:
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


def test_migrate_database_up_to_normalizes_unpadded_number(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"
    # "11" and "0011" should produce identical results
    applied_short = db.migrate_database(tmp_path / "a.db", up_to="11")
    applied_padded = db.migrate_database(tmp_path / "b.db", up_to="0011")
    assert applied_short == applied_padded
