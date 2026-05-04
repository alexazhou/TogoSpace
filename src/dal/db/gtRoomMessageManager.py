from __future__ import annotations

from model.dbModel.gtRoomMessage import GtRoomMessage
from . import gtRoomManager


async def append_room_message(
    room_id: int,
    agent_id: int,
    content: str,
    send_time: str,
    insert_immediately: bool = False,
) -> GtRoomMessage:
    return await GtRoomMessage.aio_create(
        room_id=room_id,
        agent_id=agent_id,
        content=content,
        send_time=send_time,
        insert_immediately=insert_immediately,
    )


async def get_room_messages(room_id: int, after_id: int | None = None) -> list[GtRoomMessage]:
    query = GtRoomMessage.select().where(GtRoomMessage.room_id == room_id)
    if after_id is not None:
        query = query.where(GtRoomMessage.id > after_id)
    return await query.order_by(GtRoomMessage.id.asc()).aio_execute()


async def delete_messages_by_team(team_id: int) -> int:
    """删除 Team 下所有房间的消息记录，返回删除数量。"""
    rooms = await gtRoomManager.get_rooms_by_team(team_id)
    room_ids = [room.id for room in rooms if room.id is not None]
    if not room_ids:
        return 0
    return await (
        GtRoomMessage
        .delete()
        .where(GtRoomMessage.room_id.in_(room_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )
