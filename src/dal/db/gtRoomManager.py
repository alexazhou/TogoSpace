from __future__ import annotations

from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeamMember import GtTeamMember
from util.configTypes import TeamRoomConfig
from constants import RoomType, SpecialAgent


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
            member_ids=[],
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

    # 3. 获取 team 的所有成员用于 name -> id 映射
    member_rows = await GtTeamMember.select().where(GtTeamMember.team_id == team_id).aio_execute()
    name_to_id = {m.name: m.id for m in member_rows}

    # 4. 逐个更新或插入
    for room_cfg in rooms:
        room_type = _infer_room_type_from_members(room_cfg.members)

        # 将成员名称转换为 member_ids
        member_ids: list[int] = []
        for name in room_cfg.members:
            if SpecialAgent.value_of(name) == SpecialAgent.OPERATOR:
                member_ids.append(0)  # Operator 使用 0
            elif name in name_to_id:
                member_ids.append(name_to_id[name])

        await (
            GtRoom.insert(
                team_id=team_id,
                name=room_cfg.name,
                type=room_type,
                initial_topic=room_cfg.initial_topic,
                max_turns=room_cfg.max_turns,
                member_ids=member_ids,
                updated_at=GtRoom._now_iso(),
            )
            .on_conflict(
                conflict_target=[GtRoom.team_id, GtRoom.name],
                update={
                    GtRoom.type: room_type,
                    GtRoom.initial_topic: room_cfg.initial_topic,
                    GtRoom.max_turns: room_cfg.max_turns,
                    GtRoom.member_ids: member_ids,
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


# Room Member Management (inline)
async def get_members_by_room(room_id: int) -> list[str]:
    """获取 Room 的所有成员名称。member_id=0 代表 Operator。"""
    room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
    if room is None or not room.member_ids:
        return []

    # 分离非零 ID 和 Operator (0)
    non_zero_ids = [mid for mid in room.member_ids if mid != 0]
    has_operator = any(mid == 0 for mid in room.member_ids)

    names: list[str] = []
    if non_zero_ids:
        member_rows = await GtTeamMember.select().where(
            GtTeamMember.id.in_(non_zero_ids)  # type: ignore
        ).aio_execute()
        id_to_name = {m.id: m.name for m in member_rows}
        names = [id_to_name[mid] for mid in non_zero_ids if mid in id_to_name]

    if has_operator:
        names.append("Operator")

    return sorted(names)


async def upsert_room_members(room_id: int, members: list[str]) -> None:
    """更新 Room 的成员列表（通过成员名称）。"""
    room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
    if room is None:
        return

    team_id = room.team_id
    member_rows = await GtTeamMember.select().where(GtTeamMember.team_id == team_id).aio_execute()
    name_to_id = {m.name: m.id for m in member_rows}

    member_ids: list[int] = []
    for name in members:
        if SpecialAgent.value_of(name) == SpecialAgent.OPERATOR:
            member_ids.append(0)  # Operator 使用 0
        elif name in name_to_id:
            member_ids.append(name_to_id[name])

    await GtRoom.update(member_ids=member_ids).where(GtRoom.id == room_id).aio_execute()
