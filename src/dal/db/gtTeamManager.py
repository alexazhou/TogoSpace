from __future__ import annotations

from model.dbModel.gtTeam import GtTeam


# Team CRUD
async def get_team(name: str) -> GtTeam | None:
    """获取指定 Team（未删除的）。"""
    return await GtTeam.aio_get_or_none(
        GtTeam.name == name,
        GtTeam.deleted == 0,
    )


async def get_team_by_id(team_id: int) -> GtTeam | None:
    """通过 ID 获取指定 Team。"""
    return await GtTeam.aio_get_or_none(GtTeam.id == team_id)


async def get_team_by_uuid(uuid: str, include_deleted: bool = False) -> GtTeam | None:
    """通过 UUID 获取指定 Team。

    Args:
        uuid: 团队 UUID
        include_deleted: 是否包含已删除的团队
    """
    conditions = [GtTeam.uuid == uuid]
    if not include_deleted:
        conditions.append(GtTeam.deleted == 0)
    return await GtTeam.aio_get_or_none(*conditions)


async def get_all_teams(enabled: bool | None = None) -> list[GtTeam]:
    """获取所有未删除的 Team。可通过 enabled 参数过滤。

    按 id 升序排序：最先导入的团队排在前面（is_default 团队优先展示）。
    """
    query = GtTeam.select().where(GtTeam.deleted == 0).order_by(GtTeam.id)
    if enabled is not None:
        query = query.where(GtTeam.enabled == enabled)
    return list(await query.aio_execute())


async def save_team(team: GtTeam) -> GtTeam:
    """保存 Team 对象：无 id 时插入，有 id 时更新。"""
    config = team.config or {}
    i18n = team.i18n or {}

    if team.id is None:
        team_id = await GtTeam.insert(
            name=team.name,
            uuid=team.uuid,
            config=config,
            i18n=i18n,
            enabled=team.enabled,
            deleted=team.deleted,
        ).aio_execute()
        saved = await get_team_by_id(team_id)
        assert saved is not None, f"team insert failed: name={team.name}"
        return saved

    await (
        GtTeam.update(
            name=team.name,
            uuid=team.uuid,
            config=config,
            i18n=i18n,
            enabled=team.enabled,
            deleted=team.deleted,
        )
        .where(GtTeam.id == team.id)
        .aio_execute()
    )
    saved = await get_team_by_id(team.id)
    assert saved is not None, f"team update failed: team_id={team.id}"
    return saved


async def delete_team(name: str) -> None:
    """删除 Team（设置 deleted=1）。"""
    await (
        GtTeam.update(deleted=1)
        .where(GtTeam.name == name)
        .aio_execute()
    )


async def set_team_enabled(team_id: int, enabled: bool) -> None:
    """设置 Team 的启用状态。"""
    await (
        GtTeam.update(enabled=enabled)
        .where(GtTeam.id == team_id)
        .aio_execute()
    )


async def team_exists(name: str) -> bool:
    """检查 Team 是否存在且未删除且已启用。"""
    row = await GtTeam.aio_get_or_none(
        GtTeam.name == name,
        GtTeam.deleted == 0,
        GtTeam.enabled,
    )
    return row is not None
