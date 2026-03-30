from __future__ import annotations

import asyncio
import logging

from dal.db import gtAgentHistoryManager, gtRoomMessageManager, gtRoomManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoomMessage import GtRoomMessage

logger = logging.getLogger(__name__)


async def startup() -> None:
    pass


async def shutdown() -> None:
    pass


async def append_room_message(room_id: int, agent_id: int, content: str, send_time: str) -> GtRoomMessage | None:
    return await gtRoomMessageManager.append_room_message(
        room_id=room_id,
        agent_id=agent_id,
        content=content,
        send_time=send_time,
    )


async def load_room_runtime(room_id: int) -> tuple[list[GtRoomMessage], dict[str, int] | None]:
    room_msg_rows, member_read_index = await asyncio.gather(
        gtRoomMessageManager.get_room_messages(room_id),
        gtRoomManager.get_room_state(room_id),
    )
    return room_msg_rows, member_read_index


async def save_room_runtime(room_id: int, member_read_index: dict[str, int]) -> None:
    await gtRoomManager.update_room_state(room_id, member_read_index)


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory | None:
    return await gtAgentHistoryManager.append_agent_history_message(message)


async def load_agent_history_message(agent_id: int) -> list[GtAgentHistory]:
    return await gtAgentHistoryManager.get_agent_history(agent_id)

