from __future__ import annotations

from model.dbModel.gtRoomMember import GtRoomMember


async def get_members_by_room(room_id: int) -> list[str]:
    """获取 Room 的所有成员名称。member_id=0 代表 Operator。"""
    from model.dbModel.gtTeamMember import GtTeamMember

    rows = await GtRoomMember.select().where(GtRoomMember.room_id == room_id).aio_execute()
    if not rows:
        return []

    non_zero_ids = [r.member_id for r in rows if r.member_id != 0]
    has_operator = any(r.member_id == 0 for r in rows)

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
    """创建或更新 Room 的成员。成员名称转换为 member_id 后存储，Operator 存为 0。"""
    from model.dbModel.gtRoom import GtRoom
    from model.dbModel.gtTeamMember import GtTeamMember
    from constants import SpecialAgent

    await delete_members_by_room(room_id)
    if not members:
        return

    room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
    if room is None:
        return

    team_id = room.team_id
    member_rows = await GtTeamMember.select().where(GtTeamMember.team_id == team_id).aio_execute()
    name_to_id = {m.name: m.id for m in member_rows}

    rows = []
    for name in members:
        if SpecialAgent.value_of(name) == SpecialAgent.OPERATOR:
            rows.append({"room_id": room_id, "member_id": 0})
        elif name in name_to_id:
            rows.append({"room_id": room_id, "member_id": name_to_id[name]})

    if rows:
        await GtRoomMember.insert_many(rows).aio_execute()


async def delete_members_by_room(room_id: int) -> None:
    """删除 Room 的所有成员。"""
    await GtRoomMember.delete().where(GtRoomMember.room_id == room_id).aio_execute()
