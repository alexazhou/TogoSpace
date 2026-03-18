from __future__ import annotations

from datetime import datetime

from service import orm_service


def append_agent_history_messages(agent_key: str, messages: list[dict]) -> None:
    if not messages:
        return
    conn = orm_service.get_db()
    conn.executemany(
        """
        INSERT OR IGNORE INTO agent_history_messages (agent_key, seq, message_json, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (agent_key, item["seq"], item["message_json"], datetime.now().isoformat())
            for item in messages
        ],
    )


def get_agent_history(agent_key: str) -> list[dict]:
    conn = orm_service.get_db()
    cursor = conn.execute(
        """
        SELECT id, agent_key, seq, message_json, updated_at
        FROM agent_history_messages
        WHERE agent_key = ?
        ORDER BY seq ASC
        """,
        (agent_key,),
    )
    return [dict(row) for row in cursor.fetchall()]
