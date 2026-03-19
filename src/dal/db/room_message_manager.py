from __future__ import annotations

from model.db_model.room_message import RoomMessageRecord


async def append_room_message(
    room_key: str,
    team_name: str,
    sender_name: str,
    content: str,
    send_time: str,
) -> int:
    message_id = await RoomMessageRecord.insert(
        room_key=room_key,
        team_name=team_name,
        sender_name=sender_name,
        content=content,
        send_time=send_time,
    ).aio_execute()
    return int(message_id)


async def get_room_messages(room_key: str, after_id: int | None = None) -> list[dict]:
    query = RoomMessageRecord.select().where(RoomMessageRecord.room_key == room_key)
    if after_id is not None:
        query = query.where(RoomMessageRecord.id > after_id)
    rows = await query.order_by(RoomMessageRecord.id.asc()).aio_execute()
    return [
        {
            "id": row.id,
            "room_key": row.room_key,
            "team_name": row.team_name,
            "sender_name": row.sender_name,
            "content": row.content,
            "send_time": row.send_time,
        }
        for row in rows
    ]
