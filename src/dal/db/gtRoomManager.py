from __future__ import annotations

from typing import Sequence

from model.dbModel.gtRoom import GtRoom

async def get_rooms_by_team(team_id: int) -> list[GtRoom]:
    """获取 Team 下的所有 Room。"""
    return list(
        await GtRoom.select()
        .where(GtRoom.team_id == team_id)
        .order_by(GtRoom.name)
        .aio_execute()
    )


async def get_room_by_biz_id(team_id: int, biz_id: str) -> GtRoom | None:
    """通过 biz_id 获取房间。"""
    return await GtRoom.aio_get_or_none(
        GtRoom.team_id == team_id,
        GtRoom.biz_id == biz_id,
    )


async def get_room_by_id(room_id: int) -> GtRoom | None:
    """通过主键 ID 获取房间。"""
    return await GtRoom.aio_get_or_none(GtRoom.id == room_id)


async def get_room_by_team_and_name(team_id: int, name: str) -> GtRoom | None:
    """通过 team_id + name 获取房间。"""
    return await GtRoom.aio_get_or_none(
        GtRoom.team_id == team_id,
        GtRoom.name == name,
    )


async def get_rooms_by_team_and_names(team_id: int, names: list[str]) -> list[GtRoom]:
    """通过 team_id + names 批量获取房间，按 names 顺序返回（不存在的名称自动忽略）。"""
    if not names:
        return []

    gt_rooms = list(
        await GtRoom.select()
        .where(
            GtRoom.team_id == team_id,
            GtRoom.name.in_(names),  # type: ignore[attr-defined]
        )
        .order_by(GtRoom.name)
        .aio_execute()
    )
    room_map = {room.name: room for room in gt_rooms}
    return [room_map[name] for name in names if name in room_map]


async def get_room_by_team_and_id_or_name(team_id: int, room_id: int | None, name: str) -> GtRoom | None:
    """按配置查询房间：有 room_id 则按 ID 查，否则按 name 查。"""
    condition = (GtRoom.id == room_id) if room_id is not None else (GtRoom.name == name)
    return await GtRoom.aio_get_or_none(
        GtRoom.team_id == team_id,
        condition,
    )


async def save_room(room: GtRoom) -> GtRoom:
    """保存房间对象：无 id 时插入，有 id 时更新。"""
    if room.id is None:
        room_id = await GtRoom.insert(
            team_id=room.team_id,
            name=room.name,
            type=room.type,
            initial_topic=room.initial_topic,
            max_turns=room.max_turns,
            agent_ids=room.agent_ids or [],
            agent_read_index=room.agent_read_index,
            biz_id=room.biz_id,
            tags=room.tags or [],
        ).aio_execute()
        saved = await get_room_by_id(room_id)
        assert saved is not None, f"room insert failed: team_id={room.team_id}, name={room.name}"
        return saved

    await (
        GtRoom.update(
            team_id=room.team_id,
            name=room.name,
            type=room.type,
            initial_topic=room.initial_topic,
            max_turns=room.max_turns,
            agent_ids=room.agent_ids or [],
            agent_read_index=room.agent_read_index,
            biz_id=room.biz_id,
            tags=room.tags or [],
        )
        .where(GtRoom.id == room.id)
        .aio_execute()
    )
    saved = await get_room_by_id(room.id)
    assert saved is not None, f"room update failed: room_id={room.id}"
    return saved


async def batch_save_rooms(rooms: Sequence[GtRoom]) -> None:
    """批量保存房间对象（逐个 upsert）。"""
    for room in rooms:
        await save_room(room)


async def delete_rooms_by_team(team_id: int) -> None:
    """删除 Team 下的所有 Rooms。"""
    await GtRoom.delete().where(GtRoom.team_id == team_id).aio_execute()


async def delete_room(room_id: int) -> None:
    """通过数据库 ID 删除指定 Room。"""
    await GtRoom.delete().where(GtRoom.id == room_id).aio_execute()


# Room State CRUD (persistence)
async def update_room_state(room_id: int, agent_read_index: dict[str, int], turn_pos: int = 0) -> None:
    """保存房间运行时状态（agent_read_index + turn_pos）。"""
    await (
        GtRoom.update(
            agent_read_index=agent_read_index,
            turn_pos=turn_pos,
        )
        .where(GtRoom.id == room_id)
        .aio_execute()
    )


async def get_room_state(room_id: int) -> tuple[dict[str, int] | None, int]:
    """获取房间运行时状态（agent_read_index, turn_pos）。"""
    room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
    if room is None:
        return None, 0
    return room.agent_read_index, room.turn_pos


async def delete_rooms_by_biz_ids_not_in(team_id: int, biz_ids: list[str]) -> None:
    """删除 biz_id 不在指定列表中的部门房间（只删除 tags 包含 'DEPT' 的房间）。"""
    query = GtRoom.delete().where(
        GtRoom.team_id == team_id,
        GtRoom.tags.contains("DEPT"),  # type: ignore[attr-defined]
    )

    if not biz_ids:
        # biz_ids 为空时删除 team 下所有 DEPT 房间
        await query.aio_execute()
        return

    # 兼容旧逻辑：biz_id 为 NULL 的 DEPT 房间也视为"不在列表中"，应被删除
    await query.where(
        GtRoom.biz_id.is_null(True) |  # type: ignore[attr-defined]
        (~GtRoom.biz_id.in_(biz_ids))  # type: ignore[attr-defined]
    ).aio_execute()


async def reset_room_read_index(team_id: int) -> None:
    """重置 Team 下所有房间的 agent_read_index。"""
    await (
        GtRoom.update(agent_read_index=None)
        .where(GtRoom.team_id == team_id)
        .aio_execute()
    )