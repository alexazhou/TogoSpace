from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from dal.db import gtAgentHistoryManager, gtRoomMessageManager, gtRoomManager, gtTeamManager
from model.coreModel.gtCoreChatModel import ChatMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoomMessage import GtRoomMessage
from service import ormService

logger = logging.getLogger(__name__)


async def startup() -> None:
    pass


async def shutdown() -> None:
    pass


async def append_room_message(room_id: int, sender: str, content: str, send_time: str) -> GtRoomMessage | None:
    return await gtRoomMessageManager.append_room_message(
        room_id=room_id,
        agent_name=sender,
        content=content,
        send_time=send_time,
    )


async def load_room_runtime(room_id: int) -> tuple[list[GtRoomMessage], dict[str, int] | None]:
    room_msg_rows, agent_read_index = await asyncio.gather(
        gtRoomMessageManager.get_room_messages(room_id),
        gtRoomManager.get_room_state(room_id),
    )
    return room_msg_rows, agent_read_index


async def save_room_runtime(room_id: int, agent_read_index: dict[str, int]) -> None:
    await gtRoomManager.save_room_state(room_id, agent_read_index)


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory | None:
    return await gtAgentHistoryManager.append_agent_history_message(message)


async def load_agent_history_message(team_id: int, agent_name: str) -> list[GtAgentHistory]:
    return await gtAgentHistoryManager.get_agent_history(team_id, agent_name)


async def restore_runtime_state(agents: list, rooms: list) -> None:
    for agent in agents:
        team = await gtTeamManager.get_team(agent.team_name)
        team_id = team.id if team is not None else agent.team_id
        items: list[GtAgentHistory] = await load_agent_history_message(team_id, agent.name)
        if items:
            agent.inject_history_messages(items)

    for room in rooms:
        room_msg_rows, agent_read_index = await load_room_runtime(room.room_id)
        recovered_from_db = bool(room_msg_rows)
        restored_messages: list[ChatMessage] | None = None

        if room_msg_rows:
            restored_messages = [
                ChatMessage(
                    sender_name=row.agent_name,
                    content=row.content,
                    send_time=datetime.fromisoformat(row.send_time),
                )
                for row in room_msg_rows
            ]
        elif not room.messages:
            await room.add_message("system", room.build_initial_system_message())

        if restored_messages is not None or agent_read_index is not None:
            room.inject_runtime_state(
                messages=restored_messages,
                agent_read_index=agent_read_index,
            )
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()

        room.rebuild_state_from_history()
