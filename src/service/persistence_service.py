from __future__ import annotations

import logging
from datetime import datetime

from dal.db import agent_history_manager, room_message_manager, room_state_manager
from model.chat_model import ChatMessage
from model.db_model.agent_history_message import AgentHistoryMessageRecord
from model.db_model.room_message import RoomMessageRecord
from model.db_model.room_state import RoomStateRecord
from service import orm_service

logger = logging.getLogger(__name__)

_enabled: bool = False


async def startup(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


async def shutdown() -> None:
    global _enabled
    _enabled = False


def is_enabled() -> bool:
    return _enabled and orm_service.is_ready()


async def append_room_message(room_key: str, team_name: str, sender: str, content: str, send_time: str) -> RoomMessageRecord | None:
    if not is_enabled():
        return None
    return await room_message_manager.append_room_message(
        room_key=room_key,
        team_name=team_name,
        sender_name=sender,
        content=content,
        send_time=send_time,
    )


async def save_room_state(room_key: str, agent_read_index: dict[str, int]) -> None:
    if not is_enabled():
        return
    await room_state_manager.upsert_room_state(
        room_key=room_key,
        agent_read_index=agent_read_index,
    )


async def append_agent_history_message(message: AgentHistoryMessageRecord) -> AgentHistoryMessageRecord | None:
    if not is_enabled():
        return None
    return await agent_history_manager.append_agent_history_message(message)


async def append_agent_history_messages(agent_key: str, messages: list[AgentHistoryMessageRecord]) -> None:
    if not is_enabled() or not messages:
        return
    if any(item.agent_key != agent_key for item in messages):
        raise ValueError(f"agent history items must belong to {agent_key}")
    for item in messages:
        await append_agent_history_message(item)


async def load_room_messages(room_key: str) -> list[RoomMessageRecord]:
    if not is_enabled():
        return []
    return await room_message_manager.get_room_messages(room_key)


async def load_room_state(room_key: str) -> RoomStateRecord | None:
    if not is_enabled():
        return None
    return await room_state_manager.get_room_state(room_key)


async def load_agent_history(agent_key: str) -> list[AgentHistoryMessageRecord]:
    if not is_enabled():
        return []
    return await agent_history_manager.get_agent_history(agent_key)


async def restore_runtime_state(agents: list, rooms: list) -> None:
    if not is_enabled():
        return

    for agent in agents:
        items: list[AgentHistoryMessageRecord] = await load_agent_history(agent.key)
        if items:
            agent.inject_history_messages(items)

    for room in rooms:
        room_msg_rows: list[RoomMessageRecord] = await load_room_messages(room.key)
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

        room_state: RoomStateRecord | None = await load_room_state(room.key)
        if room_state is not None:
            room.inject_agent_read_index(room_state.agent_read_index)
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()

        room.rebuild_state_from_history()
