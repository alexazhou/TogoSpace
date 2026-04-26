from __future__ import annotations

from constants import AgentActivityStatus
from model.dbModel.gtAgentActivity import GtAgentActivity


_UPDATABLE_FIELDS = {"status", "detail", "error_message", "finished_at", "duration_ms", "metadata"}


async def create_activity(item: GtAgentActivity) -> GtAgentActivity:
    """创建一条活动记录。"""
    await item.aio_save()
    return item


async def update_activity_by_id(activity_id: int, **fields) -> GtAgentActivity:
    """按 id 更新活动记录，仅允许更新指定字段。"""
    invalid = set(fields.keys()) - _UPDATABLE_FIELDS
    if invalid:
        raise ValueError(f"不允许更新以下字段: {invalid}")
    await (
        GtAgentActivity
        .update(**fields)
        .where(GtAgentActivity.id == activity_id)
        .aio_execute()
    )
    row: GtAgentActivity | None = await GtAgentActivity.aio_get_or_none(
        GtAgentActivity.id == activity_id,
    )
    if row is None:
        raise RuntimeError(f"update_activity_by_id failed: activity_id={activity_id}")
    return row


async def list_agent_activities(agent_id: int, limit: int = 100) -> list[GtAgentActivity]:
    """查询某个 Agent 的活动记录，按 id desc 排序。"""
    return await (
        GtAgentActivity
        .select()
        .where(GtAgentActivity.agent_id == agent_id)
        .order_by(GtAgentActivity.id.desc())
        .limit(limit)
        .aio_execute()
    )


async def list_agent_activities_by_status(agent_id: int, status: AgentActivityStatus, limit: int = 100) -> list[GtAgentActivity]:
    """查询某个 Agent 指定状态的活动记录，按 id desc 排序。"""
    return await (
        GtAgentActivity
        .select()
        .where(
            (GtAgentActivity.agent_id == agent_id)
            & (GtAgentActivity.status == status)
        )
        .order_by(GtAgentActivity.id.desc())
        .limit(limit)
        .aio_execute()
    )


async def list_team_activities(team_id: int, limit: int = 200) -> list[GtAgentActivity]:
    """查询某个 Team 的活动记录，按 id desc 排序。"""
    return await (
        GtAgentActivity
        .select()
        .where(GtAgentActivity.team_id == team_id)
        .order_by(GtAgentActivity.id.desc())
        .limit(limit)
        .aio_execute()
    )


async def list_activities(
    room_id: int | None = None,
    team_id: int | None = None,
    agent_id: int | None = None,
    limit: int = 200,
) -> list[GtAgentActivity]:
    """通用活动记录查询，支持按 team_id / agent_id / room_id 过滤。"""
    query = GtAgentActivity.select()
    if team_id is not None:
        query = query.where(GtAgentActivity.team_id == team_id)
    if agent_id is not None:
        query = query.where(GtAgentActivity.agent_id == agent_id)
    if room_id is not None:
        from peewee import fn
        query = query.where(fn.json_extract(GtAgentActivity.metadata, "$.room_id") == room_id)
    query = query.order_by(GtAgentActivity.id.desc()).limit(limit)
    return await query.aio_execute()
