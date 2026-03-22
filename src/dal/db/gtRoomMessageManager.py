from __future__ import annotations

from model.dbModel.gtRoomMessage import GtRoomMessage


async def append_room_message(
    room_key: str,
    team_name: str,
    sender_name: str,
    content: str,
    send_time: str,
) -> GtRoomMessage:
    return await GtRoomMessage.aio_create(
        room_key=room_key,
        team_name=team_name,
        sender_name=sender_name,
        content=content,
        send_time=send_time,
    )


async def get_room_messages(room_key: str, after_id: int | None = None) -> list[GtRoomMessage]:
    query = GtRoomMessage.select().where(GtRoomMessage.room_key == room_key)
    if after_id is not None:
        query = query.where(GtRoomMessage.id > after_id)
    return await query.order_by(GtRoomMessage.id.asc()).aio_execute()
