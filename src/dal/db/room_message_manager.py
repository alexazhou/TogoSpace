from __future__ import annotations

from model.db_model.room_message import RoomMessageRecord


async def append_room_message(
    room_key: str,
    team_name: str,
    sender_name: str,
    content: str,
    send_time: str,
) -> RoomMessageRecord:
    return await RoomMessageRecord.aio_create(
        room_key=room_key,
        team_name=team_name,
        sender_name=sender_name,
        content=content,
        send_time=send_time,
    )


async def get_room_messages(room_key: str, after_id: int | None = None) -> list[RoomMessageRecord]:
    query = RoomMessageRecord.select().where(RoomMessageRecord.room_key == room_key)
    if after_id is not None:
        query = query.where(RoomMessageRecord.id > after_id)
    return await query.order_by(RoomMessageRecord.id.asc()).aio_execute()
