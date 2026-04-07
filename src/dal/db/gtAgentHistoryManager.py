from __future__ import annotations

from constants import AgentHistoryTag
from constants import AgentHistoryStatus
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.historyUsage import HistoryUsage
from . import gtAgentManager


async def append_agent_history_message(message: GtAgentHistory) -> GtAgentHistory:
    await (
        GtAgentHistory
        .insert(
            agent_id=message.agent_id,
            seq=message.seq,
            message_json=message.message_json,
            stage=message.stage,
            status=message.status,
            error_message=message.error_message,
            tags=message.tags,
            usage=message.usage,
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


async def shift_agent_history_seq_from(agent_id: int, from_seq: int, delta: int) -> None:
    """将指定 agent 下 seq >= from_seq 的历史整体平移。

    为避免唯一索引(agent_id, seq)冲突，delta>0 时按 seq 降序更新。
    """
    if delta == 0:
        return

    rows = await (
        GtAgentHistory
        .select()
        .where(
            GtAgentHistory.agent_id == agent_id,
            GtAgentHistory.seq >= from_seq,
        )
        .order_by(
            GtAgentHistory.seq.desc() if delta > 0 else GtAgentHistory.seq.asc()  # type: ignore[attr-defined]
        )
        .aio_execute()
    )
    for row in rows:
        await (
            GtAgentHistory
            .update(seq=row.seq + delta)
            .where(GtAgentHistory.id == row.id)
            .aio_execute()
        )


async def insert_agent_history_message_at_seq(message: GtAgentHistory) -> GtAgentHistory:
    """在指定 seq 插入历史消息，并将其后的消息整体后移。"""
    await shift_agent_history_seq_from(message.agent_id, message.seq, 1)
    return await append_agent_history_message(message)


async def update_agent_history_by_id(
    history_id: int,
    *,
    message_json: dict | None = None,
    status: AgentHistoryStatus | None = None,
    error_message: str | None = None,
    tags: list[AgentHistoryTag] | None = None,
    usage: HistoryUsage | None = None,
) -> GtAgentHistory:
    update_fields: dict = {}
    if message_json is not None:
        update_fields["message_json"] = message_json
    if status is not None:
        update_fields["status"] = status
    if error_message is not None:
        update_fields["error_message"] = error_message
    if tags is not None:
        update_fields["tags"] = tags
    if usage is not None:
        update_fields["usage"] = usage
    if not update_fields:
        raise ValueError(f"update agent history by id has no fields to update: id={history_id}")

    await (
        GtAgentHistory
        .update(**update_fields)
        .where(
            GtAgentHistory.id == history_id,
        )
        .aio_execute()
    )
    row: GtAgentHistory | None = await GtAgentHistory.aio_get_or_none(
        GtAgentHistory.id == history_id,
    )
    if row is None:
        raise RuntimeError(f"update agent history status failed: id={history_id}")
    return row


async def get_agent_history(agent_id: int) -> list[GtAgentHistory]:
    return await (
        GtAgentHistory
        .select()
        .where(GtAgentHistory.agent_id == agent_id)
        .order_by(GtAgentHistory.seq.asc())  # type: ignore[attr-defined]
        .aio_execute()
    )


async def delete_history_by_team(team_id: int) -> int:
    """删除 Team 下所有 Agent 的历史记录，返回删除数量。"""
    agents = await gtAgentManager.get_team_agents(team_id)
    agent_ids = [agent.id for agent in agents if agent.id is not None]
    if not agent_ids:
        return 0
    return await (
        GtAgentHistory
        .delete()
        .where(GtAgentHistory.agent_id.in_(agent_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )
