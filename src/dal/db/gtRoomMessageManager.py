from __future__ import annotations

from datetime import datetime

from model.dbModel.gtRoomMessage import GtRoomMessage
from . import gtRoomManager


async def append_room_message(
    room_id: int,
    sender_id: int,
    content: str,
    send_time: datetime,
    insert_immediately: bool = False,
    seq: int | None = None,
    quote_id: int | None = None,
) -> GtRoomMessage:
    return await GtRoomMessage.aio_create(
        room_id=room_id,
        sender_id=sender_id,
        content=content,
        send_time=send_time,
        insert_immediately=insert_immediately,
        seq=seq,
        quote_id=quote_id,
    )


async def update_room_message_seq(message_id: int, seq: int) -> None:
    """在注入时更新 immediately 消息的 seq 字段。"""
    await (
        GtRoomMessage
        .update(seq=seq)
        .where(GtRoomMessage.id == message_id)  # type: ignore[attr-defined]
        .aio_execute()
    )


async def escalate_message_to_immediate(message_id: int) -> None:
    """将已发送的普通消息升级为 immediately 消息：重置 seq=NULL，标记 insert_immediately=True。"""
    await (
        GtRoomMessage
        .update(seq=None, insert_immediately=True)
        .where(GtRoomMessage.id == message_id)  # type: ignore[attr-defined]
        .aio_execute()
    )


async def get_room_messages(
    room_id: int,
    before_id: int | None = None,
    limit: int | None = None,
) -> tuple[list[GtRoomMessage], bool]:
    query = GtRoomMessage.select().where(GtRoomMessage.room_id == room_id)
    if before_id is not None:
        query = query.where(GtRoomMessage.id < before_id)

    has_more = False
    if limit is not None:
        rows = await (
            query
            .order_by(GtRoomMessage.seq.desc(nulls='first'), GtRoomMessage.id.desc())
            .limit(limit + 1)
            .aio_execute()
        )
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        rows.reverse()
        return rows, has_more

    rows = await query.order_by(GtRoomMessage.seq.asc(nulls='last'), GtRoomMessage.id.asc()).aio_execute()
    return rows, False


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
