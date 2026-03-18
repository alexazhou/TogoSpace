from __future__ import annotations

import json
import logging
from datetime import datetime

from model.chat_model import ChatMessage
from model.db_model.room_message import RoomMessageRecord
from service import orm_service
from dal.db import room_message_manager, room_state_manager, agent_history_manager

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


def append_room_message(room_key: str, team_name: str, sender: str, content: str, send_time: str) -> int | None:
    if not is_enabled():
        return None
    conn = orm_service.get_db()
    message_id = room_message_manager.append_room_message(
        RoomMessageRecord(
            room_key=room_key,
            team_name=team_name,
            sender_name=sender,
            content=content,
            send_time=send_time,
        )
    )
    conn.commit()
    return message_id


def save_room_state(room_key: str, agent_read_index: dict[str, int]) -> None:
    if not is_enabled():
        return
    conn = orm_service.get_db()
    room_state_manager.upsert_room_state(
        room_key=room_key,
        agent_read_index_json=json.dumps(agent_read_index, ensure_ascii=False, sort_keys=True),
    )
    conn.commit()


def append_agent_history_messages(agent_key: str, messages: list[dict]) -> None:
    if not is_enabled() or not messages:
        return
    conn = orm_service.get_db()
    agent_history_manager.append_agent_history_messages(agent_key, messages)
    conn.commit()


def load_room_messages(room_key: str) -> list[dict]:
    if not is_enabled():
        return []
    return room_message_manager.get_room_messages(room_key)


def load_room_state(room_key: str) -> dict | None:
    if not is_enabled():
        return None
    row = room_state_manager.get_room_state(room_key)
    if row is None:
        return None
    return {
        "room_key": row["room_key"],
        "agent_read_index": json.loads(row["agent_read_index_json"]),
    }


def load_agent_history(agent_key: str) -> list[dict]:
    if not is_enabled():
        return []
    return agent_history_manager.get_agent_history(agent_key)


def restore_runtime_state(agents: list, rooms: list) -> None:
    if not is_enabled():
        return

    for agent in agents:
        items = load_agent_history(agent.key)
        if items:
            agent.inject_history_messages(items)

    for room in rooms:
        room_msg_rows = load_room_messages(room.key)
        recovered_from_db = bool(room_msg_rows)
        if room_msg_rows:
            room.inject_history_messages([
                ChatMessage(
                    sender_name=row["sender_name"],
                    content=row["content"],
                    send_time=datetime.fromisoformat(row["send_time"]),
                )
                for row in room_msg_rows
            ])
        elif not room.messages:
            room.add_message("system", room.build_initial_system_message())
        room_state = load_room_state(room.key)
        if room_state is not None:
            room.inject_agent_read_index(room_state["agent_read_index"])
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()
        room.rebuild_state_from_history()
