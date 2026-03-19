from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, TypeVar

from dal.db import agent_history_manager, room_message_manager, room_state_manager
from model.chat_model import ChatMessage
from model.db_model.agent_history_message import AgentHistoryMessageRecord
from model.db_model.room_message import RoomMessageRecord
from model.db_model.room_state import RoomStateRecord
from service import orm_service

logger = logging.getLogger(__name__)

_enabled: bool = False
_pending_tasks: set[asyncio.Task] = set()
_T = TypeVar("_T")


def _on_task_done(task: asyncio.Task) -> None:
    _pending_tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("异步持久化任务执行失败")


def _track_task(task: asyncio.Task) -> None:
    _pending_tasks.add(task)
    task.add_done_callback(_on_task_done)


def dispatch(coro: Awaitable[_T]) -> _T | asyncio.Task:
    """在同步调用点触发异步持久化。

    - 若当前有 running loop：后台调度，不阻塞主流程；
    - 若当前无 running loop：直接阻塞执行，便于在同步脚本/测试中复用。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    task = loop.create_task(coro)
    _track_task(task)
    return task


async def _drain_pending_tasks() -> None:
    if not _pending_tasks:
        return
    await asyncio.gather(*list(_pending_tasks), return_exceptions=True)


async def startup(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


async def shutdown() -> None:
    global _enabled
    await _drain_pending_tasks()
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


async def append_agent_history_messages(agent_key: str, messages: list[AgentHistoryMessageRecord]) -> None:
    if not is_enabled() or not messages:
        return
    if any(item.agent_key != agent_key for item in messages):
        raise ValueError(f"agent history items must belong to {agent_key}")
    await agent_history_manager.append_agent_history_messages(messages)


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

    # 等待后台持久化任务完成，避免恢复阶段读到旧数据。
    await _drain_pending_tasks()

    for agent in agents:
        items = await load_agent_history(agent.key)
        if items:
            agent.inject_history_messages(items)

    for room in rooms:
        room_msg_rows = await load_room_messages(room.key)
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
            room.add_message("system", room.build_initial_system_message())

        room_state = await load_room_state(room.key)
        if room_state is not None:
            room.inject_agent_read_index(room_state.agent_read_index)
        elif recovered_from_db and room.messages:
            room.mark_all_messages_read()

        room.rebuild_state_from_history()
