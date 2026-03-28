from __future__ import annotations

from datetime import datetime

from model.dbModel.gtDept import GtDept


async def get_dept_by_name(team_id: int, name: str) -> GtDept | None:
    return await GtDept.aio_get_or_none(
        (GtDept.team_id == team_id) & (GtDept.name == name)
    )


async def get_all_depts(team_id: int) -> list[GtDept]:
    return list(
        await GtDept.select()
        .where(GtDept.team_id == team_id)
        .order_by(GtDept.id)
        .aio_execute()
    )


async def upsert_dept(
    team_id: int,
    name: str,
    responsibility: str,
    parent_id: int | None,
    manager_id: int,
    agent_ids: list[int],
) -> GtDept:
    now = datetime.now().isoformat()
    await (
        GtDept.insert(
            team_id=team_id,
            name=name,
            responsibility=responsibility,
            parent_id=parent_id,
            manager_id=manager_id,
            agent_ids=agent_ids,
            created_at=now,
        )
        .on_conflict(
            conflict_target=[GtDept.team_id, GtDept.name],
            update={
                GtDept.responsibility: responsibility,
                GtDept.parent_id: parent_id,
                GtDept.manager_id: manager_id,
                GtDept.agent_ids: agent_ids,
                GtDept.updated_at: now,
            },
        )
        .aio_execute()
    )
    row = await GtDept.aio_get_or_none(
        (GtDept.team_id == team_id) & (GtDept.name == name)
    )
    if row is None:
        raise RuntimeError(f"dept upsert failed: team_id={team_id}, name={name}")
    return row


async def delete_all_depts(team_id: int) -> None:
    """删除 team 下所有部门。"""
    await GtDept.delete().where(GtDept.team_id == team_id).aio_execute()
