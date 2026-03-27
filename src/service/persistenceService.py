from __future__ import annotations

import asyncio
import logging

from dal.db import gtMemberHistoryManager, gtRoomMessageManager, gtRoomManager
from model.dbModel.gtMemberHistory import GtMemberHistory
from model.dbModel.gtRoomMessage import GtRoomMessage

logger = logging.getLogger(__name__)


async def startup() -> None:
    pass


async def shutdown() -> None:
    pass


async def append_room_message(room_id: int, member_id: int, content: str, send_time: str) -> GtRoomMessage | None:
    return await gtRoomMessageManager.append_room_message(
        room_id=room_id,
        member_id=member_id,
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
    await gtRoomManager.save_room_state(room_id, member_read_index)


async def append_member_history_message(message: GtMemberHistory) -> GtMemberHistory | None:
    return await gtMemberHistoryManager.append_member_history_message(message)


async def load_member_history_message(member_id: int) -> list[GtMemberHistory]:
    return await gtMemberHistoryManager.get_member_history(member_id)


