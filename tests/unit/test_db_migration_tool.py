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
        assert {"model", "template_name"} <= _columns(conn, "agents")
        assert {"working_directory", "config"} <= _columns(conn, "teams")
    finally:
        conn.close()


def test_migrate_database_upgrades_legacy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                max_function_calls INTEGER NOT NULL DEFAULT 5,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE agent_histories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                seq INTEGER NOT NULL,
                message_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS agent_histories_team_agent_seq
            ON agent_histories(team_id, agent_name, seq);

            CREATE TABLE team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                initial_topic TEXT,
                max_turns INTEGER NOT NULL DEFAULT 100,
                agent_read_index TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE room_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL
            );

            CREATE TABLE room_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                content TEXT NOT NULL,
                send_time TEXT NOT NULL
            );

            CREATE TABLE _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO _migrations (name) VALUES ('0001.sql');
            """
        )
        conn.commit()
    finally:
        conn.close()

    applied_now = db.migrate_database(db_path)

    assert applied_now == ["0002.sql", "0003.sql", "0004.sql", "0005.sql", "0006.sql", "0007.sql", "0008.sql", "0009.sql"]

    conn = sqlite3.connect(db_path)
    try:
        assert {"model", "template_name"} <= _columns(conn, "agents")
        assert {"working_directory", "config"} <= _columns(conn, "teams")
    finally:
        conn.close()
