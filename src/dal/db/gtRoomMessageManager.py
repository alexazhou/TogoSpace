from __future__ import annotations

from datetime import datetime
import peewee
from peewee import fn

from model.dbModel.gtRoomMessage import GtRoomMessage
from . import gtRoomManager


async def append_room_message(
    room_id: int,
    sender_id: int,
    content: str,
    send_time: datetime,
    insert_immediately: bool = False,
    seq: int | None = None,
) -> GtRoomMessage:
    return await GtRoomMessage.aio_create(
        room_id=room_id,
        sender_id=sender_id,
        content=content,
        send_time=send_time,
        insert_immediately=insert_immediately,
        seq=seq,
    )


async def escalate_message_to_immediate(message_id: int) -> None:
    """将已发出的普通消息升级为 immediately 消息：只置 insert_immediately=True。

    不再重置 seq=NULL —— 顺序在消息发出的那一刻就已固定，升级改变的是
    「什么时候被读到」而不是「排在哪」。重置 seq 正是旧实现乱序的根源：
    升级后 seq 变 NULL，flush 时又拿到比原位置更靠后的新号，于是后发的
    immediate 消息反而排到了先发的普通消息前面。
    """
    await (
        GtRoomMessage
        .update(insert_immediately=True)
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
            .order_by(GtRoomMessage.seq.desc(), GtRoomMessage.id.desc())
            .limit(limit + 1)
            .aio_execute()
        )
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        rows.reverse()
        return rows, has_more

    rows = await query.order_by(GtRoomMessage.seq.asc(), GtRoomMessage.id.asc()).aio_execute()
    return rows, False


async def get_room_messages_by_seq_range(
    room_id: int,
    min_seq: int,
    max_seq: int,
) -> list[GtRoomMessage]:
    """取 min_seq <= seq < max_seq 的消息（升序），用于内存窗口越界时的缺口补读。

    左闭右开：补读区间恰为 `[读游标, 内存窗口下界)`，与内存窗口首条无缝拼接、
    不重复也不留空洞。
    """
    return list(await (
        GtRoomMessage
        .select()
        .where(
            GtRoomMessage.room_id == room_id,  # type: ignore[attr-defined]
            GtRoomMessage.seq >= min_seq,  # type: ignore[attr-defined]
            GtRoomMessage.seq < max_seq,  # type: ignore[attr-defined]
        )
        .order_by(GtRoomMessage.seq.asc(), GtRoomMessage.id.asc())
        .aio_execute()
    ))


async def get_room_message_by_id(message_id: int) -> GtRoomMessage | None:
    """按主键查单条消息，用于 escalate 在内存窗口外的 DB fallback。

    **只按 id 查、不带 room 条件** —— 调用方必须自行校验 `room_id`，
    否则可用别的房间的消息 id 触发跨房间升级（越权）。
    """
    rows = await (
        GtRoomMessage
        .select()
        .where(GtRoomMessage.id == message_id)  # type: ignore[attr-defined]
        .limit(1)
        .aio_execute()
    )
    return rows[0] if rows else None


async def count_room_messages(room_id: int) -> int:
    """统计房间消息**总数**。

    内存窗口化后 `len(room.messages)` 只等于窗口大小，不再等于真实总数；
    任何需要「真实总数」的地方（如房间详情统计）必须走本方法。
    """
    return await (
        GtRoomMessage
        .select()
        .where(GtRoomMessage.room_id == room_id)  # type: ignore[attr-defined]
        .aio_count()
    )


async def get_last_messages_by_room_ids(room_ids: list[int]) -> list[GtRoomMessage]:
    """批量获取多个房间的最后一条消息，保持 room_ids 原始顺序。"""
    if not room_ids:
        return []

    room_message_fields = tuple(GtRoomMessage._meta.sorted_fields)
    window = peewee.Window(
        partition_by=[GtRoomMessage.room_id],
        order_by=[
            GtRoomMessage.seq.desc(),
            GtRoomMessage.id.desc(),
        ],
    ).alias("last_message_window")
    ranked_messages = (
        GtRoomMessage
        .select(
            *room_message_fields,
            fn.ROW_NUMBER().over(window).alias("rn"),
        )
        .where(GtRoomMessage.room_id.in_(room_ids))  # type: ignore[attr-defined]
        .window(window)
        .alias("ranked_messages")
    )
    rows = list(await (
        GtRoomMessage
        .select(
            *[getattr(ranked_messages.c, field.name) for field in room_message_fields],
        )
        .from_(ranked_messages)
        .where(ranked_messages.c.rn == 1)
        .aio_execute()
    ))
    row_map = {row.room_id: row for row in rows}
    return [row_map[room_id] for room_id in room_ids if room_id in row_map]


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
