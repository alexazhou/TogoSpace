from __future__ import annotations

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


async def save_room(room_id: int, agent_read_index: dict[str, int]) -> None:
    await gtRoomManager.save_room_state(room_id, agent_read_index)


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory | None:
    return await gtAgentHistoryManager.append_agent_history_message(message)


async def load_room_messages(room_id: int) -> list[GtRoomMessage]:
    return await gtRoomMessageManager.get_room_messages(room_id)


async def load_room_state(room_id: int) -> dict[str, int] | None:
    return await gtRoomManager.get_room_state(room_id)


async def load_agent_history(team_id: int, agent_name: str) -> list[GtAgentHistory]:
    return await gtAgentHistoryManager.get_agent_history(team_id, agent_name)


def _parse_agent_key(agent_key: str) -> tuple[str, str | None]:
    if "@" not in agent_key:
        return agent_key, None
    agent_name, team_name = agent_key.split("@", 1)
    return agent_name, team_name


async def restore_runtime_state(agents: list, rooms: list) -> None:
    for agent in agents:
        team = await gtTeamManager.get_team(agent.team_name)
        team_id = team.id if team is not None else agent.team_id
        items: list[GtAgentHistory] = await load_agent_history(team_id, agent.name)
        if items:
            agent.inject_history_messages(items)

    for room in rooms:
        room_msg_rows: list[GtRoomMessage] = await load_room_messages(room.room_id)
        recovered_from_db = bool(room_msg_rows)
        if room_msg_rows:
            room.inject_history_messages([
                ChatMessage(
                    sender_name=row.agent_name,
                    content=row.content,
                    send_time=datetime.fromisoformat(row.send_time),
                )
                for row in room_msg_rows
            ])
        elif not room.messages:
            await room.add_message("system", room.build_initial_system_message())

        agent_read_index = await load_room_state(room.room_id)
        if agent_read_index is not None:
            room.inject_agent_read_index(agent_read_index)
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()

        room.rebuild_state_from_history()


async def append_agent_history_messages(
    team_or_agent_key: int | str,
    agent_name_or_messages,
    messages: list[GtAgentHistory] | None = None,
) -> None:
    if messages is None:
        agent_name, _ = _parse_agent_key(str(team_or_agent_key))
        items = list(agent_name_or_messages)
        for item in items:
            item.agent_name = agent_name
        await gtAgentHistoryManager.append_agent_history_messages(items)
        return

    team_id = int(team_or_agent_key)
    agent_name = str(agent_name_or_messages)
    items = list(messages)
    for item in items:
        item.team_id = team_id
        item.agent_name = agent_name
    await gtAgentHistoryManager.append_agent_history_messages(items)
