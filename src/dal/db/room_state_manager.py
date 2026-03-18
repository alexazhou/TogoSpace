from __future__ import annotations

import json
from datetime import datetime

from service import orm_service


def upsert_room_state(room_key: str, agent_read_index_json: str) -> None:
    conn = orm_service.get_db()
    conn.execute(
        """
        INSERT INTO room_states (room_key, agent_read_index_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(room_key) DO UPDATE SET
          agent_read_index_json = excluded.agent_read_index_json,
          updated_at = excluded.updated_at
        """,
        (room_key, agent_read_index_json, datetime.now().isoformat()),
    )


def get_room_state(room_key: str) -> dict | None:
    conn = orm_service.get_db()
    cursor = conn.execute(
        """
        SELECT room_key, agent_read_index_json, updated_at
        FROM room_states
        WHERE room_key = ?
        """,
        (room_key,),
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None
