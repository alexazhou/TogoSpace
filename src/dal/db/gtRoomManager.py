from __future__ import annotations

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
        (GtRoom.team_id == team_id) & (GtRoom.biz_id == biz_id)
    )


async def get_room_by_id(room_id: int) -> GtRoom | None:
    """通过主键 ID 获取房间。"""
    return await GtRoom.aio_get_or_none(GtRoom.id == room_id)


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
            updated_at=GtRoom._now(),
        )
        .where(GtRoom.id == room.id)
        .aio_execute()
    )
    saved = await get_room_by_id(room.id)
    assert saved is not None, f"room update failed: room_id={room.id}"
    return saved


async def batch_save_rooms(rooms: list[GtRoom]) -> None:
    """批量保存房间对象。"""
    for room in rooms:
        await save_room(room)


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
            updated_at=GtRoom._now(),
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


async def delete_rooms_by_biz_ids_not_in(team_id: int, biz_ids: list[str]) -> None:
    """删除 biz_id 不在指定列表中的部门房间（只删除 tags 包含 'DEPT' 的房间）。"""
    if not biz_ids:
        # 如果 biz_ids 为空，删除所有 DEPT 房间
        await (
            GtRoom.delete()
            .where(
                (GtRoom.team_id == team_id) &
                (GtRoom.tags.contains("DEPT"))  # type: ignore[attr-defined]
            )
            .aio_execute()
        )
        return

    # 查找所有 tags 包含 "DEPT" 的房间
    dept_rooms = await GtRoom.select().where(
        (GtRoom.team_id == team_id) &
        (GtRoom.tags.contains("DEPT"))  # type: ignore[attr-defined]
    ).aio_execute()

    # 删除 biz_id 不在列表中的房间
    to_delete = [r.id for r in dept_rooms if r.biz_id not in biz_ids]
    if to_delete:
        await GtRoom.delete().where(GtRoom.id.in_(to_delete)).aio_execute()  # type: ignore
