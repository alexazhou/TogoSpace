"""持久化服务：负责运行时状态的恢复。

写入操作由各业务模块直接调用 dal manager 完成，本模块仅提供恢复相关的加载方法。
"""
from __future__ import annotations

import asyncio

from dal.db import gtAgentHistoryManager, gtRoomMessageManager, gtRoomManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoomMessage import GtRoomMessage


async def startup() -> None:
    pass


async def shutdown() -> None:
    pass


async def load_room_runtime(room_id: int) -> tuple[list[GtRoomMessage], dict[str, int] | None]:
    """加载房间的聊天记录和成员读取进度。

    Returns:
        (room_messages, agent_read_index) - 消息列表和成员读取位置映射
    """
    gt_room_messages, agent_read_index = await asyncio.gather(
        gtRoomMessageManager.get_room_messages(room_id),
        gtRoomManager.get_room_state(room_id),
    )
    return gt_room_messages, agent_read_index


async def load_agent_history_message(agent_id: int) -> list[GtAgentHistory]:
    """加载 Agent 的对话历史。"""
    return await gtAgentHistoryManager.get_agent_history(agent_id)