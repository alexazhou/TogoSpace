from __future__ import annotations

from model.db_model.room_message import RoomMessageRecord
from service import orm_service


def append_room_message(message: RoomMessageRecord) -> int:
    conn = orm_service.get_db()
    cursor = conn.execute(
        """
        INSERT INTO room_messages (room_key, team_name, sender_name, content, send_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message.room_key, message.team_name, message.sender_name, message.content, message.send_time),
    )
    return int(cursor.lastrowid)


def get_room_messages(room_key: str, after_id: int | None = None) -> list[dict]:
    conn = orm_service.get_db()
    if after_id is None:
        cursor = conn.execute(
            """
            SELECT id, room_key, team_name, sender_name, content, send_time
            FROM room_messages
            WHERE room_key = ?
            ORDER BY id ASC
            """,
            (room_key,),
        )
    else:
        cursor = conn.execute(
            """
            SELECT id, room_key, team_name, sender_name, content, send_time
            FROM room_messages
            WHERE room_key = ? AND id > ?
            ORDER BY id ASC
            """,
            (room_key, after_id),
        )
    return [dict(row) for row in cursor.fetchall()]
