from __future__ import annotations

from datetime import datetime

from peewee import EXCLUDED

from model.db_model.room_state import RoomStateRecord


async def upsert_room_state(room_key: str, agent_read_index_json: str) -> None:
    now = datetime.now().isoformat()
    await (
        RoomStateRecord.insert(
            room_key=room_key,
            agent_read_index_json=agent_read_index_json,
            updated_at=now,
        )
        .on_conflict(
            conflict_target=[RoomStateRecord.room_key],
            update={
                RoomStateRecord.agent_read_index_json: EXCLUDED.agent_read_index_json,
                RoomStateRecord.updated_at: EXCLUDED.updated_at,
            },
        )
        .aio_execute()
    )


async def get_room_state(room_key: str) -> dict | None:
    row = await RoomStateRecord.aio_get_or_none(RoomStateRecord.room_key == room_key)
    if row is None:
        return None
    return {
        "room_key": row.room_key,
        "agent_read_index_json": row.agent_read_index_json,
        "updated_at": row.updated_at,
    }
