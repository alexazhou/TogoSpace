from __future__ import annotations

from model.dbModel.gtRoomMessage import GtRoomMessage


async def append_room_message(
    room_id: int,
    member_id: int,
    content: str,
    send_time: str,
) -> GtRoomMessage:
    return await GtRoomMessage.aio_create(
        room_id=room_id,
        member_id=member_id,
        content=content,
        send_time=send_time,
    )


async def get_room_messages(room_id: int, after_id: int | None = None) -> list[GtRoomMessage]:
    query = GtRoomMessage.select().where(GtRoomMessage.room_id == room_id)
    if after_id is not None:
        query = query.where(GtRoomMessage.id > after_id)
    return await query.order_by(GtRoomMessage.id.asc()).aio_execute()
