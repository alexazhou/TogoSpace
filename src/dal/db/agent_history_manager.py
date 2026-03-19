from __future__ import annotations

from model.db_model.agent_history_message import AgentHistoryMessageRecord


async def append_agent_history_message(message: AgentHistoryMessageRecord) -> AgentHistoryMessageRecord:
    await (
        AgentHistoryMessageRecord
        .insert(
            agent_key=message.agent_key,
            seq=message.seq,
            message_json=message.message_json,
        )
        .on_conflict_ignore()
        .aio_execute()
    )
    row: AgentHistoryMessageRecord | None = await AgentHistoryMessageRecord.aio_get_or_none(
        (AgentHistoryMessageRecord.agent_key == message.agent_key) &
        (AgentHistoryMessageRecord.seq == message.seq)
    )
    if row is None:
        raise RuntimeError(f"append agent history failed: {message.agent_key}#{message.seq}")
    return row


async def append_agent_history_messages(messages: list[AgentHistoryMessageRecord]) -> list[AgentHistoryMessageRecord]:
    if not messages:
        return []
    rows: list[AgentHistoryMessageRecord] = []
    for item in messages:
        rows.append(await append_agent_history_message(item))
    return rows


async def get_agent_history(agent_key: str) -> list[AgentHistoryMessageRecord]:
    return await (
        AgentHistoryMessageRecord
        .select()
        .where(AgentHistoryMessageRecord.agent_key == agent_key)
        .order_by(AgentHistoryMessageRecord.seq.asc())
        .aio_execute()
    )
