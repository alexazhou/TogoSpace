from __future__ import annotations

from constants import AgentTaskStatus, AgentTaskType
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


async def get_pending_tasks(agent_id: int) -> list[GtAgentTask]:
    """获取 Agent 的待处理任务列表。"""
    return await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.agent_id == agent_id,
            GtAgentTask.status == AgentTaskStatus.PENDING,
        )
        .order_by(GtAgentTask.id.asc())
        .aio_execute()
    )


async def get_running_task(agent_id: int) -> GtAgentTask | None:
    """获取 Agent 正在处理的任务。"""
    return await GtAgentTask.aio_get_or_none(
        GtAgentTask.agent_id == agent_id,
        GtAgentTask.status == AgentTaskStatus.RUNNING,
    )


async def get_pending_and_running_tasks(agent_id: int) -> list[GtAgentTask]:
    """获取 Agent 的待处理和正在处理的任务（用于恢复）。"""
    return await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.agent_id == agent_id,
            GtAgentTask.status.in_([AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING]),
        )
        .order_by(GtAgentTask.id.asc())
        .aio_execute()
    )


async def delete_task(task_id: int) -> None:
    """删除任务记录。"""
    await (
        GtAgentTask
        .delete()
        .where(GtAgentTask.id == task_id)
        .aio_execute()
    )