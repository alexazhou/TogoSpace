from __future__ import annotations

from model.dbModel.gtAgentHistory import GtAgentHistory


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory:
    await (
        GtAgentHistory
        .insert(
            team_id=message.team_id,
            agent_name=message.agent_name,
            seq=message.seq,
            message_json=message.message_json,
        )
        .on_conflict_ignore()
        .aio_execute()
    )
    row: GtAgentHistory | None = await GtAgentHistory.aio_get_or_none(
        (GtAgentHistory.team_id == message.team_id) &
        (GtAgentHistory.agent_name == message.agent_name) &
        (GtAgentHistory.seq == message.seq)
    )
    if row is None:
        raise RuntimeError(f"append agent history failed: {message.agent_name}@{message.team_id}#{message.seq}")
    return row


async def append_agent_history_messages(messages: list[GtAgentHistory]) -> list[GtAgentHistory]:
    if not messages:
        return []
    rows: list[GtAgentHistory] = []
    for item in messages:
        rows.append(await append_agent_history_message(item))
    return rows


async def get_agent_history(team_id: int, agent_name: str) -> list[GtAgentHistory]:
    return await (
        GtAgentHistory
        .select()
        .where(
            (GtAgentHistory.team_id == team_id) &
            (GtAgentHistory.agent_name == agent_name)
        )
        .order_by(GtAgentHistory.seq.asc())
        .aio_execute()
    )
