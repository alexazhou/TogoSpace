from __future__ import annotations

from peewee import EXCLUDED

from model.dbModel.gtRoom import GtRoom
from constants import RoomType


# Room Config CRUD
async def get_rooms_by_team(team_name: str) -> list[GtRoom]:
    """获取 Team 下的所有 Room。"""
    return list(
        await GtRoom.select()
        .where(GtRoom.team_name == team_name)
        .order_by(GtRoom.name)
        .aio_execute()
    )


async def get_room_config(room_key: str) -> GtRoom | None:
    """获取指定 Room 的配置。"""
    return await GtRoom.aio_get_or_none(GtRoom.room_key == room_key)


async def upsert_rooms(team_name: str, rooms: list) -> None:
    """创建或更新 Team 下的 Rooms。"""
    # 先删除旧数据
    await delete_rooms_by_team(team_name)

    # 插入新数据
    rows = []
    for room in rooms:
        room_name = room["name"]
        room_key = f"{room_name}@{team_name}"
        room_type = RoomType(room.get("type", "group"))
        initial_topic = room.get("initial_topic", "")
        max_turns = room.get("max_turns", 100)

        rows.append({
            "room_key": room_key,
            "team_name": team_name,
            "name": room_name,
            "type": room_type,
            "initial_topic": initial_topic,
            "max_turns": max_turns,
        })

    if rows:
        await GtRoom.insert_many(rows).aio_execute()


async def delete_rooms_by_team(team_name: str) -> None:
    """删除 Team 下的所有 Rooms。"""
    await GtRoom.delete().where(GtRoom.team_name == team_name).aio_execute()


async def delete_room(room_key: str) -> None:
    """删除指定 Room。"""
    await GtRoom.delete().where(GtRoom.room_key == room_key).aio_execute()


# Room State CRUD (persistence)
async def save_room_state(room_key: str, agent_read_index: dict[str, int]) -> None:
    """保存房间运行时状态（agent_read_index）。"""
    await (
        GtRoom.update(
            agent_read_index=agent_read_index,
            updated_at=GtRoom._now_iso(),
        )
        .where(GtRoom.room_key == room_key)
        .aio_execute()
    )


async def get_room_state(room_key: str) -> dict[str, int] | None:
    """获取房间运行时状态（agent_read_index）。"""
    room = await GtRoom.aio_get_or_none(GtRoom.room_key == room_key)
    if room is None:
        return None
    return room.agent_read_index