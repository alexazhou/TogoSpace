from __future__ import annotations

from model.db_model.agent_history_message import AgentHistoryMessageRecord


async def append_agent_history_messages(agent_key: str, messages: list[dict]) -> None:
    if not messages:
        return
    payload = [
        {
            "agent_key": agent_key,
            "seq": item["seq"],
            "message_json": item["message_json"],
        }
        for item in messages
    ]
    await AgentHistoryMessageRecord.insert_many(payload).on_conflict_ignore().aio_execute()


async def get_agent_history(agent_key: str) -> list[dict]:
    rows = await (
        AgentHistoryMessageRecord
        .select()
        .where(AgentHistoryMessageRecord.agent_key == agent_key)
        .order_by(AgentHistoryMessageRecord.seq.asc())
        .aio_execute()
    )
    return [
        {
            "id": row.id,
            "agent_key": row.agent_key,
            "seq": row.seq,
            "message_json": row.message_json,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]
