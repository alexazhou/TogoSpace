from __future__ import annotations

from constants import AgentJobStatus, AgentJobType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtScheduleJob import GtScheduleJob


async def create_job(
    agent_id: int,
    job_type: AgentJobType,
    task_data: dict,
) -> GtScheduleJob:
    """创建 Agent 调度任务记录。"""
    job = GtScheduleJob(
        agent_id=agent_id,
        job_type=job_type,
        task_data=task_data,
        status=AgentJobStatus.PENDING,
    )
    await job.aio_save()
    return job


async def has_pending_room_job(
    agent_id: int,
    room_id: int,
    *,
    include_failed: bool = False,
) -> bool:
    """检查 Agent 是否已存在同房间的 PENDING 调度任务。

    Args:
        include_failed: 为 True 时，也将 FAILED 任务计入检查范围（用于防止重复创建任务）。
    """
    statuses = [AgentJobStatus.PENDING]
    if include_failed:
        statuses.append(AgentJobStatus.FAILED)
    jobs = await (
        GtScheduleJob
        .select()
        .where(
            GtScheduleJob.agent_id == agent_id,
            GtScheduleJob.status.in_(statuses),  # type: ignore[attr-defined]
        )
        .order_by(GtScheduleJob.id.asc())  # type: ignore[attr-defined]
        .aio_execute()
    )
    return any(job.task_data.get("room_id") == room_id for job in jobs)


async def get_first_unfinished_job(agent_id: int) -> GtScheduleJob | None:
    """获取 Agent 最早的未完成调度任务，按优先级排序。

    未完成任务当前定义为 PENDING / RUNNING / FAILED。
    排序规则：job_type ASC（ROOM_MESSAGE 优先于 TODO_TASK），再按 id ASC。
    这样只要有消息任务待处理，Agent 会优先回消息；消息处理完后才会处理协作任务。
    """
    return await (
        GtScheduleJob
        .select()
        .where(
            GtScheduleJob.agent_id == agent_id,
            GtScheduleJob.status.in_([AgentJobStatus.PENDING, AgentJobStatus.RUNNING, AgentJobStatus.FAILED]),  # type: ignore[attr-defined]
        )
        .order_by(GtScheduleJob.job_type.asc(), GtScheduleJob.id.asc())  # type: ignore[attr-defined]
        .aio_first()
    )


async def has_consumable_job(agent_id: int) -> bool:
    """检查 Agent 是否仍有可继续消费的待处理调度任务。

    该判断复用 get_first_unfinished_job() 的规则：
    - 最早的未完成任务若为 FAILED，则不再视为可继续消费
    - 仅当最早的未完成任务为可认领的 PENDING 时返回 True
    """
    first_job = await get_first_unfinished_job(agent_id)
    return first_job is not None and first_job.status == AgentJobStatus.PENDING


async def has_pending_collaboration_job(agent_id: int, agent_task_id: int) -> bool:
    """检查是否已存在对应 agent_task_id 的 PENDING TODO_TASK 调度记录（幂等检查）。"""
    jobs = await (
        GtScheduleJob
        .select()
        .where(
            GtScheduleJob.agent_id == agent_id,
            GtScheduleJob.job_type == AgentJobType.TODO_TASK,
            GtScheduleJob.status == AgentJobStatus.PENDING,
        )
        .aio_execute()
    )
    return any(job.task_data.get("agent_task_id") == agent_task_id for job in jobs)


async def transition_job_status(
    job_id: int,
    from_status: AgentJobStatus,
    to_status: AgentJobStatus,
) -> GtScheduleJob | None:
    """原子地迁移调度任务状态。

    仅当任务当前状态等于 ``from_status`` 时，才会更新为 ``to_status``。
    若任务状态已变化，则返回 None。
    """
    result = await (
        GtScheduleJob
        .update(status=to_status)
        .where(
            GtScheduleJob.id == job_id,
            GtScheduleJob.status == from_status,
        )
        .aio_execute()
    )
    if result == 0:
        return None
    return await GtScheduleJob.aio_get_or_none(GtScheduleJob.id == job_id)


async def get_running_jobs(agent_id: int) -> list[GtScheduleJob]:
    """获取 Agent 的 RUNNING 调度任务（用于启动恢复）。"""
    return await (
        GtScheduleJob
        .select()
        .where(
            GtScheduleJob.agent_id == agent_id,
            GtScheduleJob.status == AgentJobStatus.RUNNING,
        )
        .order_by(GtScheduleJob.id.asc())  # type: ignore[attr-defined]
        .aio_execute()
    )


async def update_job_status(
    job_id: int,
    status: AgentJobStatus,
    error_message: str | None = None,
) -> GtScheduleJob:
    """更新调度任务状态。"""
    update_fields: dict = {"status": status}
    if error_message is not None:
        update_fields["error_message"] = error_message

    await (
        GtScheduleJob
        .update(**update_fields)
        .where(GtScheduleJob.id == job_id)
        .aio_execute()
    )
    row: GtScheduleJob | None = await GtScheduleJob.aio_get_or_none(
        GtScheduleJob.id == job_id,
    )
    if row is None:
        raise RuntimeError(f"update job status failed: job_id={job_id}")
    return row


async def delete_jobs_by_team(team_id: int) -> int:
    """删除 Team 下所有 Agent 的调度任务记录，返回删除数量。"""
    agent_ids_query = (
        GtAgent
        .select(GtAgent.id)
        .where(GtAgent.team_id == team_id)
    )
    return await (
        GtScheduleJob
        .delete()
        .where(GtScheduleJob.agent_id.in_(agent_ids_query))  # type: ignore[attr-defined]
        .aio_execute()
    )


async def get_job(job_id: int) -> GtScheduleJob | None:
    """按主键查询调度任务。"""
    return await GtScheduleJob.aio_get_or_none(GtScheduleJob.id == job_id)
