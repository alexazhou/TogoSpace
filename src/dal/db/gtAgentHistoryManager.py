from __future__ import annotations

from model.dbModel.gtAgentHistory import GtAgentHistory


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory:
    await (
        GtAgentHistory
        .insert(
            agent_id=message.agent_id,
            seq=message.seq,
            message_json=message.message_json,
        )
        .on_conflict_ignore()
        .aio_execute()
    )
    row: GtAgentHistory | None = await GtAgentHistory.aio_get_or_none(
        GtAgentHistory.agent_id == message.agent_id,
        GtAgentHistory.seq == message.seq,
    )
    if row is None:
        raise RuntimeError(f"append agent history failed: agent_id={message.agent_id}#{message.seq}")
    return row


async def get_agent_history(agent_id: int) -> list[GtAgentHistory]:
    return await (
        GtAgentHistory
        .select()
        .where(GtAgentHistory.agent_id == agent_id)
        .order_by(GtAgentHistory.seq.asc())  # type: ignore[attr-defined]
        .aio_execute()
    )
