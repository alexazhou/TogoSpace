from __future__ import annotations

from constants import AgentTaskStatus, AgentTaskType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask


async def create_task(
    agent_id: int,
    task_type: AgentTaskType,
    task_data: dict,
) -> GtAgentTask:
    """创建 Agent 任务记录。"""
    task = GtAgentTask(
        agent_id=agent_id,
        task_type=task_type,
        task_data=task_data,
        status=AgentTaskStatus.PENDING,
    )
    await task.aio_save()
    return task


async def has_pending_room_task(agent_id: int, room_id: int) -> bool:
    """检查 Agent 是否已存在同房间的 PENDING 任务。"""
    tasks = await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.agent_id == agent_id,
            GtAgentTask.status == AgentTaskStatus.PENDING,
        )
        .order_by(GtAgentTask.id.asc())
        .aio_execute()
    )
    return any(task.task_data.get("room_id") == room_id for task in tasks)


async def get_first_unfinish_task(agent_id: int) -> GtAgentTask | None:
    """获取 Agent 最早的未完成任务。

    未完成任务当前定义为 PENDING 或 FAILED。
    这样失败任务会按顺序阻断后续任务，但调用方仍能拿到该失败任务本身。
    """
    return await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.agent_id == agent_id,
            GtAgentTask.status.in_([AgentTaskStatus.PENDING, AgentTaskStatus.FAILED]),
        )
        .order_by(GtAgentTask.id.asc())
        .aio_first()
    )


async def has_consumable_task(agent_id: int) -> bool:
    """检查 Agent 是否仍有可继续消费的待处理任务。

    该判断复用 get_first_unfinish_task() 的规则：
    - 最早的未完成任务若为 FAILED，则不再视为可继续消费
    - 仅当最早的未完成任务为可认领的 PENDING 时返回 True
    """
    first_task = await get_first_unfinish_task(agent_id)
    return first_task is not None and first_task.status == AgentTaskStatus.PENDING


async def claim_task(task_id: int) -> GtAgentTask | None:
    """原子地认领任务：将 PENDING 状态改为 RUNNING。

    使用乐观锁保证只有一个消费者能成功认领。
    返回更新后的任务，如果任务已被其他消费者认领则返回 None。
    """
    result = await (
        GtAgentTask
        .update(status=AgentTaskStatus.RUNNING)
        .where(
            GtAgentTask.id == task_id,
            GtAgentTask.status == AgentTaskStatus.PENDING,
        )
        .aio_execute()
    )
    if result == 0:
        return None
    return await GtAgentTask.aio_get_or_none(GtAgentTask.id == task_id)


async def get_running_tasks(agent_id: int) -> list[GtAgentTask]:
    """获取 Agent 的 RUNNING 任务（用于启动恢复）。"""
    return await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.agent_id == agent_id,
            GtAgentTask.status == AgentTaskStatus.RUNNING,
        )
        .order_by(GtAgentTask.id.asc())
        .aio_execute()
    )


async def update_task_status(
    task_id: int,
    status: AgentTaskStatus,
    error_message: str | None = None,
) -> GtAgentTask:
    """更新任务状态。"""
    update_fields: dict = {"status": status}
    if error_message is not None:
        update_fields["error_message"] = error_message

    await (
        GtAgentTask
        .update(**update_fields)
        .where(GtAgentTask.id == task_id)
        .aio_execute()
    )
    row: GtAgentTask | None = await GtAgentTask.aio_get_or_none(
        GtAgentTask.id == task_id,
    )
    if row is None:
        raise RuntimeError(f"update task status failed: task_id={task_id}")
    return row


async def delete_tasks_by_team(team_id: int) -> int:
    """删除 Team 下所有 Agent 的任务记录，返回删除数量。"""
    agent_ids_query = (
        GtAgent
        .select(GtAgent.id)
        .where(GtAgent.team_id == team_id)
    )
    return await (
        GtAgentTask
        .delete()
        .where(GtAgentTask.agent_id.in_(agent_ids_query))  # type: ignore[attr-defined]
        .aio_execute()
    )
