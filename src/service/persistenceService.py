from __future__ import annotations

import logging
from datetime import datetime

from dal.db import gtAgentHistoryManager, gtRoomMessageManager, gtRoomManager
from model.coreModel.gtCoreChatModel import ChatMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoomMessage import GtRoomMessage
from service import ormService, roomService

logger = logging.getLogger(__name__)

_restore_on_startup: bool = False


async def startup(restore_on_startup: bool) -> None:
    global _restore_on_startup
    _restore_on_startup = restore_on_startup


async def shutdown() -> None:
    global _restore_on_startup
    _restore_on_startup = False


def is_enabled() -> bool:
    return ormService.is_ready()


async def append_room_message(room_key: str, sender: str, content: str, send_time: str) -> GtRoomMessage | None:
    if not is_enabled():
        return None
    db_id = roomService.get_room_db_id(room_key)
    return await gtRoomMessageManager.append_room_message(
        room_id=db_id,
        sender_name=sender,
        content=content,
        send_time=send_time,
    )


async def save_room(room_key: str, agent_read_index: dict[str, int]) -> None:
    if not is_enabled():
        return
    db_id = roomService.get_room_db_id(room_key)
    await gtRoomManager.save_room_state(db_id, agent_read_index)


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory | None:
    if not is_enabled():
        return None
    return await gtAgentHistoryManager.append_agent_history_message(message)


async def append_agent_history_messages(agent_key: str, messages: list[GtAgentHistory]) -> None:
    if not is_enabled() or not messages:
        return
    if any(item.agent_key != agent_key for item in messages):
        raise ValueError(f"agent history items must belong to {agent_key}")
    for item in messages:
        await append_agent_history_message(item)


async def load_room_messages(room_key: str) -> list[GtRoomMessage]:
    if not is_enabled():
        return []
    db_id = roomService.get_room_db_id(room_key)
    return await gtRoomMessageManager.get_room_messages(db_id)


async def load_room_state(room_key: str) -> dict[str, int] | None:
    if not is_enabled():
        return None
    db_id = roomService.get_room_db_id(room_key)
    return await gtRoomManager.get_room_state(db_id)


async def load_agent_history(agent_key: str) -> list[GtAgentHistory]:
    if not is_enabled():
        return []
    return await gtAgentHistoryManager.get_agent_history(agent_key)


async def restore_runtime_state(agents: list, rooms: list) -> None:
    if not _restore_on_startup:
        return

    for agent in agents:
        items: list[GtAgentHistory] = await load_agent_history(agent.key)
        if items:
            agent.inject_history_messages(items)

    for room in rooms:
        room_msg_rows: list[GtRoomMessage] = await load_room_messages(room.key)
        recovered_from_db = bool(room_msg_rows)
        if room_msg_rows:
            room.inject_history_messages([
                ChatMessage(
                    sender_name=row.sender_name,
                    content=row.content,
                    send_time=datetime.fromisoformat(row.send_time),
                )
                for row in room_msg_rows
            ])
        elif not room.messages:
            await room.add_message("system", room.build_initial_system_message())

        agent_read_index = await load_room_state(room.key)
        if agent_read_index is not None:
            room.inject_agent_read_index(agent_read_index)
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()

        room.rebuild_state_from_history()
