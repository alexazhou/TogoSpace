from __future__ import annotations

from model.dbModel.gtRoomMember import GtRoomMember


async def get_members_by_room(room_id: int) -> list[str]:
    """获取 Room 的所有成员名称。"""
    rows = await GtRoomMember.select(GtRoomMember.agent_name).where(
        GtRoomMember.room_id == room_id
    ).order_by(GtRoomMember.agent_name).aio_execute()

    return [row.agent_name for row in rows]


async def upsert_room_members(room_id: int, members: list[str]) -> None:
    """创建或更新 Room 的成员。"""
    # 先删除旧数据
    await delete_members_by_room(room_id)

    # 插入新数据
    if members:
        rows = [
            {"room_id": room_id, "agent_name": member}
            for member in members
        ]
        await GtRoomMember.insert_many(rows).aio_execute()


async def delete_members_by_room(room_id: int) -> None:
    """删除 Room 的所有成员。"""
    await GtRoomMember.delete().where(GtRoomMember.room_id == room_id).aio_execute()