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


async def get_first_pending_task(agent_id: int) -> GtAgentTask | None:
    """获取 Agent 的第一个待处理任务。

    如果存在失败的任务，则不返回任何 pending 任务（不能跳过失败任务）。
    """
    # 先检查是否有失败的任务
    failed_task = await GtAgentTask.aio_get_or_none(
        GtAgentTask.agent_id == agent_id,
        GtAgentTask.status == AgentTaskStatus.FAILED,
    )
    if failed_task is not None:
        return None

    return await GtAgentTask.aio_get_or_none(
        GtAgentTask.agent_id == agent_id,
        GtAgentTask.status == AgentTaskStatus.PENDING,
    )


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


async def has_pending_or_running_tasks(agent_id: int) -> bool:
    """检查 Agent 是否有待处理或正在处理的任务。"""
    count = await (
        GtAgentTask
        .select()
        .where(
            GtAgentTask.agent_id == agent_id,
            GtAgentTask.status.in_([AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING]),
        )
        .aio_count()
    )
    return count > 0