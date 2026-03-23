from __future__ import annotations

from peewee import EXCLUDED

from model.dbModel.gtRoom import GtRoom
from constants import RoomType


# Room Config CRUD
async def get_rooms_by_team(team_id: int) -> list[GtRoom]:
    """获取 Team 下的所有 Room。"""
    return list(
        await GtRoom.select()
        .where(GtRoom.team_id == team_id)
        .order_by(GtRoom.name)
        .aio_execute()
    )


async def get_room_config(team_id: int, room_name: str) -> GtRoom | None:
    """通过 team_id 和 room_name 获取 Room 配置。"""
    return await GtRoom.aio_get_or_none(
        (GtRoom.team_id == team_id) & (GtRoom.name == room_name)
    )


async def ensure_room_by_key(
    team_id: int,
    room_name: str,
    room_type: RoomType,
    initial_topic: str,
    max_turns: int,
) -> GtRoom:
    """确保 (team_id, name) 对应的 Room 存在，由 DB 自增分配 id；返回 GtRoom 行。"""
    await (
        GtRoom.insert(
            team_id=team_id,
            name=room_name,
            type=room_type,
            initial_topic=initial_topic,
            max_turns=max_turns,
        )
        .on_conflict(
            conflict_target=[GtRoom.team_id, GtRoom.name],
            update={
                GtRoom.type: room_type,
                GtRoom.initial_topic: initial_topic,
                GtRoom.max_turns: max_turns,
                GtRoom.updated_at: GtRoom._now_iso(),
            },
        )
        .aio_execute()
    )
    return await GtRoom.aio_get(
        (GtRoom.team_id == team_id) & (GtRoom.name == room_name)
    )


async def upsert_rooms(team_id: int, rooms: list) -> None:
    """创建或更新 Team 下的 Rooms。"""
    # 先删除旧数据
    await delete_rooms_by_team(team_id)

    # 插入新数据
    rows = []
    for room in rooms:
        room_name = room["name"]
        room_type = RoomType.value_of(room.get("type", "group")) or RoomType.GROUP
        initial_topic = room.get("initial_topic", "")
        max_turns = room.get("max_turns", 100)
        updated_at = GtRoom._now_iso()

        rows.append({
            "team_id": team_id,
            "name": room_name,
            "type": room_type.value,
            "initial_topic": initial_topic,
            "max_turns": max_turns,
            "updated_at": updated_at,
        })

    if rows:
        await GtRoom.insert_many(rows).aio_execute()


async def delete_rooms_by_team(team_id: int) -> None:
    """删除 Team 下的所有 Rooms。"""
    await GtRoom.delete().where(GtRoom.team_id == team_id).aio_execute()


async def delete_room(room_id: int) -> None:
    """通过数据库 ID 删除指定 Room。"""
    await GtRoom.delete().where(GtRoom.id == room_id).aio_execute()


# Room State CRUD (persistence)
async def save_room_state(room_id: int, agent_read_index: dict[str, int]) -> None:
    """保存房间运行时状态（agent_read_index）。"""
    await (
        GtRoom.update(
            agent_read_index=agent_read_index,
            updated_at=GtRoom._now_iso(),
        )
        .where(GtRoom.id == room_id)
        .aio_execute()
    )


async def get_room_state(room_id: int) -> dict[str, int] | None:
    """获取房间运行时状态（agent_read_index）。"""
    room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
    if room is None:
        return None
    return room.agent_read_index
