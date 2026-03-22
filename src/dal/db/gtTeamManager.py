from __future__ import annotations

import logging

from model.dbModel.gtTeam import GtTeam

logger = logging.getLogger(__name__)


# Team CRUD
async def get_team(name: str) -> GtTeam | None:
    """获取指定 Team。"""
    return await GtTeam.aio_get_or_none(GtTeam.name == name)


async def get_team_by_id(team_id: int) -> GtTeam | None:
    """通过 ID 获取指定 Team。"""
    return await GtTeam.aio_get_or_none(GtTeam.id == team_id)


async def get_all_teams() -> list[GtTeam]:
    """获取所有启用的 Team。"""
    return list(
        await GtTeam.select()
        .where(GtTeam.enabled == 1)
        .order_by(GtTeam.name)
        .aio_execute()
    )


async def upsert_team(team_config: dict) -> GtTeam:
    """创建或更新 Team。"""
    name = team_config["name"]
    max_function_calls = team_config.get("max_function_calls")

    await (
        GtTeam.insert(
            name=name,
            max_function_calls=max_function_calls,
            enabled=1,
            created_at=GtTeam._now_iso(),
            updated_at=GtTeam._now_iso(),
        )
        .on_conflict(
            conflict_target=[GtTeam.name],
            update={
                GtTeam.max_function_calls: max_function_calls,
                GtTeam.updated_at: GtTeam._now_iso(),
            },
        )
        .aio_execute()
    )

    row = await GtTeam.aio_get_or_none(GtTeam.name == name)
    if row is None:
        raise RuntimeError(f"team upsert failed: {name}")
    return row


async def delete_team(name: str) -> None:
    """软删除 Team（设置 enabled=0）。"""
    await (
        GtTeam.update(enabled=0, updated_at=GtTeam._now_iso())
        .where(GtTeam.name == name)
        .aio_execute()
    )


async def team_exists(name: str) -> bool:
    """检查 Team 是否存在且已启用。"""
    row = await GtTeam.aio_get_or_none((GtTeam.name == name) & (GtTeam.enabled == 1))
    return row is not None


# 完整配置获取
async def get_team_config(name: str) -> dict | None:
    """获取指定 Team 的完整配置（类似 JSON 格式）。"""
    from dal.db import gtRoomManager, gtRoomMemberManager

    team = await get_team(name)
    if team is None:
        return None

    rooms = []
    for room in await gtRoomManager.get_rooms_by_team(name):
        members = await gtRoomMemberManager.get_members_by_room(room.room_id)
        rooms.append({
            "name": room.name,
            "type": room.type.name,
            "initial_topic": room.initial_topic,
            "max_turns": room.max_turns,
            "members": members,
        })

    return {
        "name": team.name,
        "max_function_calls": team.max_function_calls,
        "rooms": rooms,
    }


async def get_all_team_configs() -> list[dict]:
    """获取所有 Team 的完整配置列表。"""
    result = []
    for team in await get_all_teams():
        config = await get_team_config(team.name)
        if config:
            result.append(config)
    return result


# JSON 到数据库的转换
async def import_team_from_json(team_config: dict) -> None:
    """从 JSON 配置导入 Team 到数据库。"""
    from dal.db import gtRoomManager, gtRoomMemberManager

    name = team_config["name"]

    # 检查是否已存在
    existing = await get_team(name)
    if existing is not None:
        logger.info(f"Team '{name}' 已存在，跳过导入")
        return

    # 导入 Team
    await upsert_team(team_config)

    # 导入 Rooms
    rooms = team_config.get("rooms", [])
    await gtRoomManager.upsert_rooms(name, rooms)

    # 导入 Members 并设置 room_key -> db_id 映射
    for room in rooms:
        room_name = room["name"]
        room_config = await gtRoomManager.get_room_config(name, room_name)
        if room_config:
            members = room.get("members", [])
            await gtRoomMemberManager.upsert_room_members(room_config.id, members)
            room_key = f"{room_name}@{name}"
            from service import roomService as rs
            rs.set_room_db_id(room_key, room_config.id)

    logger.info(f"Team '{name}' 已从 JSON 导入数据库")