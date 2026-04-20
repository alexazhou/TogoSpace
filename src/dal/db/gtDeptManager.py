from __future__ import annotations

from model.dbModel.gtDept import GtDept


async def get_dept_by_name(team_id: int, name: str) -> GtDept | None:
    return await GtDept.aio_get_or_none(
        GtDept.team_id == team_id,
        GtDept.name == name,
    )


async def get_all_depts(team_id: int) -> list[GtDept]:
    return list(
        await GtDept.select()
        .where(GtDept.team_id == team_id)
        .order_by(GtDept.id)
        .aio_execute()
    )


async def save_dept(
    team_id: int,
    name: str,
    responsibility: str,
    parent_id: int | None,
    manager_id: int,
    agent_ids: list[int],
    dept_id: int | None = None,
    i18n: dict | None = None,
) -> GtDept:
    """创建或更新部门。如果提供 dept_id，则按 ID 更新现有部门；否则按 name 创建/更新。"""
    if dept_id is not None:
        # 按 ID 更新现有部门
        update_data = {
            GtDept.name: name,
            GtDept.responsibility: responsibility,
            GtDept.parent_id: parent_id,
            GtDept.manager_id: manager_id,
            GtDept.agent_ids: agent_ids,
        }
        if i18n is not None:
            update_data[GtDept.i18n] = i18n
        await (
            GtDept.update(update_data)
            .where(GtDept.id == dept_id)
            .aio_execute()
        )
        row = await GtDept.aio_get_or_none(GtDept.id == dept_id)
        if row is None:
            raise RuntimeError(f"dept update failed: dept_id={dept_id}")
        return row

    # 按 name 创建/更新
    insert_data = {
        GtDept.team_id: team_id,
        GtDept.name: name,
        GtDept.responsibility: responsibility,
        GtDept.parent_id: parent_id,
        GtDept.manager_id: manager_id,
        GtDept.agent_ids: agent_ids,
        GtDept.i18n: i18n or {},
    }
    update_data = {
        GtDept.responsibility: responsibility,
        GtDept.parent_id: parent_id,
        GtDept.manager_id: manager_id,
        GtDept.agent_ids: agent_ids,
    }
    if i18n is not None:
        update_data[GtDept.i18n] = i18n
    await (
        GtDept.insert(insert_data)
        .on_conflict(
            conflict_target=[GtDept.team_id, GtDept.name],
            update=update_data,
        )
        .aio_execute()
    )
    row = await GtDept.aio_get_or_none(
        GtDept.team_id == team_id,
        GtDept.name == name,
    )
    if row is None:
        raise RuntimeError(f"dept upsert failed: team_id={team_id}, name={name}")
    return row


async def delete_all_depts(team_id: int) -> None:
    """删除 team 下所有部门。"""
    await GtDept.delete().where(GtDept.team_id == team_id).aio_execute()
