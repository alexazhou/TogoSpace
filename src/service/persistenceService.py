"""持久化服务：负责运行时状态的恢复。

写入操作由各业务模块直接调用 dal manager 完成，本模块仅提供恢复相关的加载方法。
"""
from __future__ import annotations

import asyncio

from dal.db import gtAgentHistoryManager, gtAgentTaskManager, gtRoomMessageManager, gtRoomManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from constants import AgentHistoryTag, AgentTaskStatus


async def startup() -> None:
    pass


async def shutdown() -> None:
    pass


async def load_room_runtime(room_id: int) -> tuple[list[GtRoomMessage], dict[str, int] | None, int]:
    """加载房间的聊天记录、成员读取进度和发言位索引。

    Returns:
        (room_messages, agent_read_index, turn_pos)
    """
    gt_room_messages, (agent_read_index, turn_pos) = await asyncio.gather(
        gtRoomMessageManager.get_room_messages(room_id),
        gtRoomManager.get_room_state(room_id),
    )
    return gt_room_messages, agent_read_index, turn_pos


async def load_agent_history_message(agent_id: int) -> list[GtAgentHistory]:
    """加载 Agent 的对话历史，启动恢复时按 compact 规则裁剪加载范围。

    若存在 COMPACT_CMD tag 的消息，只返回最新 COMPACT_CMD 及其之后的消息。
    """
    items = await gtAgentHistoryManager.get_agent_history(agent_id)
    return _trim_to_latest_compact(items)


async def fail_running_tasks(agent_id: int) -> None:
    """将 Agent 的 RUNNING 任务标记为 FAILED（用于启动时恢复）。"""
    tasks = await gtAgentTaskManager.get_running_tasks(agent_id)
    for task in tasks:
        await gtAgentTaskManager.update_task_status(
            task.id,
            AgentTaskStatus.FAILED,
            error_message="task interrupted by process restart",
        )


def _trim_to_latest_compact(items: list[GtAgentHistory]) -> list[GtAgentHistory]:
    """若存在 COMPACT_CMD，只保留最新 COMPACT_CMD 及其之后的消息。"""
    for idx in range(len(items) - 1, -1, -1):
        if AgentHistoryTag.COMPACT_CMD in items[idx].tags:
            return items[idx:]
    return items
