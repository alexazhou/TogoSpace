from __future__ import annotations

from peewee import EXCLUDED

from model.db_model.room_state import RoomStateRecord


async def upsert_room_state(room_key: str, agent_read_index: dict[str, int]) -> RoomStateRecord:
    await (
        RoomStateRecord.insert(
            room_key=room_key,
            agent_read_index=agent_read_index,
        )
        .on_conflict(
            conflict_target=[RoomStateRecord.room_key],
            update={
                RoomStateRecord.agent_read_index: EXCLUDED.agent_read_index,
                RoomStateRecord.updated_at: EXCLUDED.updated_at,
            },
        )
        .aio_execute()
    )
    row = await RoomStateRecord.aio_get_or_none(RoomStateRecord.room_key == room_key)
    if row is None:
        raise RuntimeError(f"room state upsert failed: {room_key}")
    return row


async def get_room_state(room_key: str) -> RoomStateRecord | None:
    return await RoomStateRecord.aio_get_or_none(RoomStateRecord.room_key == room_key)
