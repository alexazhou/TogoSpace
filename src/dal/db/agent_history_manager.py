from __future__ import annotations

from model.db_model.agent_history_message import AgentHistoryMessageRecord


async def append_agent_history_messages(messages: list[AgentHistoryMessageRecord]) -> list[AgentHistoryMessageRecord]:
    if not messages:
        return []
    agent_key = messages[0].agent_key
    payload = [
        {
            "agent_key": item.agent_key,
            "seq": item.seq,
            "message_json": item.message_json,
        }
        for item in messages
    ]
    await AgentHistoryMessageRecord.insert_many(payload).on_conflict_ignore().aio_execute()
    seq_list = [item.seq for item in messages]
    return await (
        AgentHistoryMessageRecord
        .select()
        .where(
            (AgentHistoryMessageRecord.agent_key == agent_key) &
            (AgentHistoryMessageRecord.seq.in_(seq_list))
        )
        .order_by(AgentHistoryMessageRecord.seq.asc())
        .aio_execute()
    )


async def get_agent_history(agent_key: str) -> list[AgentHistoryMessageRecord]:
    return await (
        AgentHistoryMessageRecord
        .select()
        .where(AgentHistoryMessageRecord.agent_key == agent_key)
        .order_by(AgentHistoryMessageRecord.seq.asc())
        .aio_execute()
    )
