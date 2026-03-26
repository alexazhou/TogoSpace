from __future__ import annotations

from model.dbModel.gtRoom import GtRoom
from constants import RoomType, SpecialAgent
from util.configTypes import TeamRoomConfig


def _infer_room_type_from_members(members: list[str]) -> RoomType:
    normalized = {m.upper() for m in (members or [])}
    # 约定：仅当包含 Operator 且仅有 1 个非 Operator 成员时判定为 PRIVATE
    ai_count = len([m for m in normalized if SpecialAgent.value_of(m) != SpecialAgent.OPERATOR])
    has_operator = any(SpecialAgent.value_of(m) == SpecialAgent.OPERATOR for m in normalized)
    if has_operator and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


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


async def upsert_rooms(team_id: int, rooms: list[TeamRoomConfig]) -> None:
    """创建或更新 Team 下的 Rooms。使用 surgical update 确保已存在的 Room ID 不变。"""
    # 1. 获取当前数据库中的所有 Room
    current_rooms = await get_rooms_by_team(team_id)
    current_names = {r.name: r.id for r in current_rooms}
    new_names = {r.name for r in rooms}

    # 2. 删除不在新配置中的 Room
    to_delete = [rid for name, rid in current_names.items() if name not in new_names]
    if to_delete:
        await GtRoom.delete().where(GtRoom.id.in_(to_delete)).aio_execute()  # type: ignore

    # 3. 逐个更新或插入
    for room_cfg in rooms:
        room_type = _infer_room_type_from_members(room_cfg.members)
        
        await (
            GtRoom.insert(
                team_id=team_id,
                name=room_cfg.name,
                type=room_type,
                initial_topic=room_cfg.initial_topic,
                max_turns=room_cfg.max_turns,
                updated_at=GtRoom._now_iso(),
            )
            .on_conflict(
                conflict_target=[GtRoom.team_id, GtRoom.name],
                update={
                    GtRoom.type: room_type,
                    GtRoom.initial_topic: room_cfg.initial_topic,
                    GtRoom.max_turns: room_cfg.max_turns,
                    GtRoom.updated_at: GtRoom._now_iso(),
                },
            )
            .aio_execute()
        )


async def delete_rooms_by_team(team_id: int) -> None:
    """删除 Team 下的所有 Rooms。"""
    await GtRoom.delete().where(GtRoom.team_id == team_id).aio_execute()


async def delete_room(room_id: int) -> None:
    """通过数据库 ID 删除指定 Room。"""
    await GtRoom.delete().where(GtRoom.id == room_id).aio_execute()


# Room State CRUD (persistence)
async def save_room_state(room_id: int, member_read_index: dict[str, int]) -> None:
    """保存房间运行时状态（member_read_index）。"""
    await (
        GtRoom.update(
            member_read_index=member_read_index,
            updated_at=GtRoom._now_iso(),
        )
        .where(GtRoom.id == room_id)
        .aio_execute()
    )


async def get_room_state(room_id: int) -> dict[str, int] | None:
    """获取房间运行时状态（member_read_index）。"""
    room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
    if room is None:
        return None
    return room.member_read_index
