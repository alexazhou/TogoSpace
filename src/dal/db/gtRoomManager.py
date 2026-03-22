from __future__ import annotations

from peewee import EXCLUDED

from model.dbModel.gtRoom import GtRoom


async def upsert_room(room_key: str, agent_read_index: dict[str, int]) -> GtRoom:
    await (
        GtRoom.insert(
            room_key=room_key,
            agent_read_index=agent_read_index,
        )
        .on_conflict(
            conflict_target=[GtRoom.room_key],
            update={
                GtRoom.agent_read_index: EXCLUDED.agent_read_index,
                GtRoom.updated_at: EXCLUDED.updated_at,
            },
        )
        .aio_execute()
    )
    row = await GtRoom.aio_get_or_none(GtRoom.room_key == room_key)
    if row is None:
        raise RuntimeError(f"room upsert failed: {room_key}")
    return row


async def get_room(room_key: str) -> GtRoom | None:
    return await GtRoom.aio_get_or_none(GtRoom.room_key == room_key)
