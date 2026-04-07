"""AgentHistoryStore 单元测试：测试纯内存操作（不依赖数据库）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from constants import AgentHistoryTag, AgentHistoryStatus, OpenaiApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from util import llmApiUtil


def _make_item(
    message: llmApiUtil.OpenAIMessage,
    *,
    agent_id: int = 1,
    seq: int = 0,
    status: AgentHistoryStatus | None = None,
    tags: list[AgentHistoryTag] | None = None,
) -> GtAgentHistory:
    """测试辅助函数：创建 GtAgentHistory 并填充 agent_id 和 seq。"""
    item = GtAgentHistory.build(message, status=status, tags=tags)
    item.agent_id = agent_id
    item.seq = seq
    return item


def test_agent_history_last_role_returns_none_for_empty_history():
    history = AgentHistoryStore(agent_id=1)

    assert history.last() is None
    assert history._last_role() is None


def test_agent_history_openai_message_round_trips():
    user_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")
    tool_msg = llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}')
    history = AgentHistoryStore(
        agent_id=2,
        items=[
            _make_item(user_msg, agent_id=2, seq=0),
            _make_item(tool_msg, agent_id=2, seq=1),
        ],
    )

    exported = [item.openai_message for item in history]

    assert [msg.role for msg in exported] == [OpenaiApiRole.USER, OpenaiApiRole.TOOL]
    assert [msg.content for msg in exported] == ["u1", '{"success": true}']


def test_agent_history_get_last_assistant_message_respects_start_index():
    history = AgentHistoryStore(
        agent_id=3,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), agent_id=3, seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), agent_id=3, seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), agent_id=3, seq=2),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a2"), agent_id=3, seq=3),
        ],
    )

    last_any = history.get_last_assistant_message()
    last_from_two = history.get_last_assistant_message(start_idx=2)

    assert last_any is not None
    assert last_any.content == "a2"
    assert last_from_two is not None
    assert last_from_two.content == "a2"


def test_agent_history_find_tool_result_by_call_id_returns_matching_history_item():
    history = AgentHistoryStore(
        agent_id=4,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), agent_id=4, seq=0),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}'), agent_id=4, seq=1),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_2", '{"success": false}'), agent_id=4, seq=2),
        ],
    )

    item = history.find_tool_result_by_call_id("call_2")

    assert item is not None
    assert item.tool_call_id == "call_2"
    assert item.content == '{"success": false}'
    assert item.role == OpenaiApiRole.TOOL
    assert history.find_tool_result_by_call_id("missing") is None


def test_agent_history_len_and_iter():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
        ],
    )

    assert len(history) == 3
    contents = [item.content for item in history]
    assert contents == ["u1", "a1", "u2"]


def test_agent_history_getitem():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
        ],
    )

    assert history[0].content == "u1"
    assert history[1].content == "a1"
    assert history[-1].content == "a1"


def test_agent_history_replace():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
        ],
    )

    new_items = [
        _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "new1"), seq=0),
        _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "new2"), seq=1),
    ]
    history.replace(new_items)

    assert len(history) == 2
    items = list(history)
    assert items[0].content == "new1"
    assert items[1].content == "new2"


def test_agent_history_find_tool_call_by_id():
    tool_call = llmApiUtil.OpenAIToolCall(
        id="call_123",
        function={"name": "send_chat_msg", "arguments": '{"msg": "hello"}'},
    )
    assistant_msg = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="",
        tool_calls=[tool_call],
    )

    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            _make_item(assistant_msg, seq=1),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_123", '{"ok": true}'), seq=2),
        ],
    )

    found = history.find_tool_call_by_id("call_123")
    assert found is not None
    assert found.id == "call_123"
    assert found.function["name"] == "send_chat_msg"

    assert history.find_tool_call_by_id("nonexistent") is None
    assert history.find_tool_call_by_id("") is None


def test_agent_history_unfinished_turn_with_items():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "done"), seq=2, tags=[AgentHistoryTag.ROOM_TURN_FINISH]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=3, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
        ],
    )

    assert history.has_active_turn() is True
    assert history.get_current_turn_start_index() == 3


# ─── Compact 相关方法 ────────────────────────────────────


def test_build_infer_messages_without_compact_returns_all():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
        ],
    )
    msgs = history.build_infer_messages()
    assert [msg.content for msg in msgs] == ["u1", "a1"]


def test_build_infer_messages_with_compact_includes_summary_and_after():
    # 不变量：COMPACT_SUMMARY 在 _items[0]，build_infer_messages 返回所有消息
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"), seq=0, tags=[AgentHistoryTag.COMPACT_SUMMARY]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep last"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "next"), seq=2),
        ],
    )

    msgs = history.build_infer_messages()

    assert [msg.content for msg in msgs] == ["compact summary", "keep last", "next"]


def test_build_infer_messages_without_compact_returns_all_2():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep last"), seq=2),
        ],
    )

    msgs = history.build_infer_messages()

    assert [msg.content for msg in msgs] == ["old1", "old2", "keep last"]


def test_build_compact_source_messages_skips_trailing_user():
    """末尾是 USER 时，跳过 USER 压缩前面的消息。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 2


def test_build_compact_compress_all_when_trailing_is_assistant():
    """末尾是 ASSISTANT 时压缩全部。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a2"), seq=3),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", "u2", "a2"]
    assert plan.insert_seq == 0


def test_build_compact_compress_all_when_trailing_is_tool():
    """末尾是 TOOL 时压缩全部。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", '{"ok": true}'), seq=2),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", '{"ok": true}']
    assert plan.insert_seq == 0


def test_build_compact_skips_multiple_trailing_users():
    """末尾多个连续 USER 时，全部跳过。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u3"), seq=3),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 2


def test_build_compact_excludes_pending_infer_and_compress_all():
    """pending infer 被排除后，末尾是 ASSISTANT，压缩全部。"""
    pending_infer = _make_item(
        llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT),
        seq=2,
        status=AgentHistoryStatus.INIT,
    )
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            pending_infer,
        ],
    )

    plan = history.build_compact_plan()

    # pending infer 被排除，剩下 [u1, a1]，末尾是 ASSISTANT，压缩全部
    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 0


def test_build_compact_preserves_tool_call_chain_when_trailing_is_user():
    """末尾是 USER 时，tool call 链在压缩后自然完整。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "tool call"), seq=3),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", '{"ok": true}'), seq=4),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u3"), seq=5),
        ],
    )

    plan = history.build_compact_plan()

    # 跳过 u3，压缩 u3 之前的全部
    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", "u2", "tool call", '{"ok": true}']
    assert plan.insert_seq == 5


def test_build_compact_insert_seq_when_trailing_is_user():
    """末尾是 USER 时，insert_seq 是被保留的第一条消息的 seq。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
        ],
    )

    assert history.build_compact_plan().insert_seq == 2


def test_build_infer_messages_excludes_pending_infer_tail():
    pending_infer = _make_item(
        llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT),
        seq=2,
        status=AgentHistoryStatus.FAILED,
    )
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            pending_infer,
        ],
    )

    msgs = history.build_infer_messages()

    assert [msg.content for msg in msgs] == ["u1", "a1"]


def test_trim_to_compact_window_keeps_compact_suffix():
    # _trim_to_compact_window 在 insert_compact_summary 中调用，确保 COMPACT_SUMMARY 落在 index=0
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"), seq=2, tags=[AgentHistoryTag.COMPACT_SUMMARY]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=3),
        ],
    )

    history._trim_to_compact_window()

    assert [item.content for item in history] == ["compact summary", "keep"]


@pytest.mark.asyncio
async def test_append_history_message_with_seq_inserts_at_position():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=1),
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.insert_agent_history_message_at_seq",
        AsyncMock(side_effect=lambda item: item),
    ):
        inserted = await history.append_history_message(
            GtAgentHistory.build(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "mid")),
            seq=1,
        )

    assert inserted.seq == 1
    assert [item.seq for item in history] == [0, 1, 2]
    assert [item.content for item in history] == ["u1", "mid", "u2"]


@pytest.mark.asyncio
async def test_append_history_message_uses_last_seq_after_compact_trim():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"), seq=1, tags=[AgentHistoryTag.COMPACT_SUMMARY]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep1"), seq=2),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "keep2"), seq=3),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=4),
        ],
    )
    history._trim_to_compact_window()

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    ):
        appended = await history.append_history_message(
            GtAgentHistory.build(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "next")),
        )

    assert appended.seq == 5
    assert [item.seq for item in history] == [1, 2, 3, 4, 5]