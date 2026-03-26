from __future__ import annotations

from model.dbModel.gtMemberHistory import GtMemberHistory


async def append_member_history_message(message: GtMemberHistory) -> GtMemberHistory:
    await (
        GtMemberHistory
        .insert(
            team_id=message.team_id,
            member_name=message.member_name,
            seq=message.seq,
            message_json=message.message_json,
        )
        .on_conflict_ignore()
        .aio_execute()
    )
    row: GtMemberHistory | None = await GtMemberHistory.aio_get_or_none(
        (GtMemberHistory.team_id == message.team_id) &
        (GtMemberHistory.member_name == message.member_name) &
        (GtMemberHistory.seq == message.seq)
    )
    if row is None:
        raise RuntimeError(f"append member history failed: {message.member_name}@{message.team_id}#{message.seq}")
    return row


async def get_member_history(team_id: int, member_name: str) -> list[GtMemberHistory]:
    return await (
        GtMemberHistory
        .select()
        .where(
            (GtMemberHistory.team_id == team_id) &
            (GtMemberHistory.member_name == member_name)
        )
        .order_by(GtMemberHistory.seq.asc())
        .aio_execute()
    )
