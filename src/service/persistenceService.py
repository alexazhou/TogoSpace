"""持久化服务：负责运行时状态的恢复。

写入操作由各业务模块直接调用 dal manager 完成，本模块仅提供恢复相关的加载方法。
"""
from __future__ import annotations

import asyncio

from dal.db import gtAgentHistoryManager, gtAgentTaskManager, gtRoomMessageManager, gtRoomManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoomMessage import GtRoomMessage
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

    若存在 compact 记录，只返回恢复当前 compact 视图所需的最小消息窗口。
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
    """按 compact 视图规则裁剪恢复窗口。从 DB 加载时 COMPACT_SUMMARY 可在任意位置，需扫描定位。"""
    for idx in range(len(items) - 1, -1, -1):
        if AgentHistoryTag.COMPACT_SUMMARY in items[idx].tags:
            return items[idx:]
    return items
