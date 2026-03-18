import json
import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

_conn: Optional[sqlite3.Connection] = None
_db_path: Optional[str] = None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS room_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          room_key TEXT NOT NULL,
          team_name TEXT NOT NULL,
          sender_name TEXT NOT NULL,
          content TEXT NOT NULL,
          send_time TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_room_messages_room_key_id
        ON room_messages(room_key, id);

        CREATE TABLE IF NOT EXISTS room_states (
          room_key TEXT PRIMARY KEY,
          agent_read_index_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_history_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          agent_key TEXT NOT NULL,
          seq INTEGER NOT NULL,
          message_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_history_agent_seq
        ON agent_history_messages(agent_key, seq);
        """
    )
    conn.commit()


async def startup(db_path: str) -> None:
    global _conn, _db_path
    if _conn is not None:
        return

    _db_path = db_path
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    _conn = sqlite3.connect(abs_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _ensure_schema(_conn)
    logger.info("ORM service started: db=%s", abs_path)


async def shutdown() -> None:
    global _conn, _db_path
    if _conn is not None:
        _conn.close()
    _conn = None
    _db_path = None


def get_db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("orm_service not started")
    return _conn


def is_ready() -> bool:
    return _conn is not None


def get_db_path() -> Optional[str]:
    return _db_path
