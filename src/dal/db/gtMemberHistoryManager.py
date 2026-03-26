from __future__ import annotations

from model.dbModel.gtMemberHistory import GtMemberHistory


async def append_member_history_message(message: GtMemberHistory) -> GtMemberHistory:
    await (
        GtMemberHistory
        .insert(
            member_id=message.member_id,
            seq=message.seq,
            message_json=message.message_json,
        )
        .on_conflict_ignore()
        .aio_execute()
    )
    row: GtMemberHistory | None = await GtMemberHistory.aio_get_or_none(
        (GtMemberHistory.member_id == message.member_id) &
        (GtMemberHistory.seq == message.seq)
    )
    if row is None:
        raise RuntimeError(f"append member history failed: member_id={message.member_id}#{message.seq}")
    return row


async def get_member_history(member_id: int) -> list[GtMemberHistory]:
    return await (
        GtMemberHistory
        .select()
        .where(GtMemberHistory.member_id == member_id)
        .order_by(GtMemberHistory.seq.asc())
        .aio_execute()
    )
