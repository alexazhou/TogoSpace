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
        assert {"model", "template_name", "soul", "type", "driver", "allowed_tools"} <= _columns(conn, "role_templates")
        agent_columns = _columns(conn, "agents")
        assert {"role_template_id"} <= agent_columns
        assert "role_template_name" not in agent_columns
        assert {"config", "max_function_calls"} <= _columns(conn, "teams")
    finally:
        conn.close()
