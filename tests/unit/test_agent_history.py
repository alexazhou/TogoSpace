"""AgentHistoryStore 单元测试：测试纯内存操作（不依赖数据库）。"""
from unittest.mock import AsyncMock, patch

import pytest

from constants import AgentHistoryTag, AgentHistoryStage, AgentHistoryStatus, OpenaiApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from util import llmApiUtil


def test_agent_history_last_role_returns_none_for_empty_history():
    history = AgentHistoryStore(agent_id=1)

    assert history.last() is None
    assert history.last_role() is None


def test_agent_history_openai_message_round_trips():
    user_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")
    tool_msg = llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}')
    history = AgentHistoryStore(
        agent_id=2,
        items=[
            GtAgentHistory.from_openai_message(2, 0, user_msg),
            GtAgentHistory.from_openai_message(2, 1, tool_msg),
        ],
    )

    exported = [item.openai_message for item in history]

    assert [msg.role for msg in exported] == [OpenaiApiRole.USER, OpenaiApiRole.TOOL]
    assert [msg.content for msg in exported] == ["u1", '{"success": true}']


def test_agent_history_get_last_assistant_message_respects_start_index():
    history = AgentHistoryStore(
        agent_id=3,
        items=[
            GtAgentHistory.from_openai_message(3, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(3, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(3, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2")),
            GtAgentHistory.from_openai_message(3, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a2")),
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
            GtAgentHistory.from_openai_message(4, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(4, 1, llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}')),
            GtAgentHistory.from_openai_message(4, 2, llmApiUtil.OpenAIMessage.tool_result("call_2", '{"success": false}')),
        ],
    )

    item = history.find_tool_result_by_call_id("call_2")

    assert item is not None
    assert item.tool_call_id == "call_2"
    assert item.content == '{"success": false}'
    assert item.stage == AgentHistoryStage.TOOL_RESULT
    assert history.find_tool_result_by_call_id("missing") is None


def test_from_openai_message_assigns_stage_by_role():
    user_item = GtAgentHistory.from_openai_message(
        9,
        0,
        llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u"),
    )
    assistant_item = GtAgentHistory.from_openai_message(
        9,
        1,
        llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a"),
    )
    tool_item = GtAgentHistory.from_openai_message(
        9,
        2,
        llmApiUtil.OpenAIMessage.tool_result("c1", '{"success": true}'),
    )

    assert user_item.stage == AgentHistoryStage.INPUT
    assert assistant_item.stage == AgentHistoryStage.INFER
    assert tool_item.stage == AgentHistoryStage.TOOL_RESULT


def test_agent_history_len_and_iter():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2")),
        ],
    )

    assert len(history) == 3
    contents = [item.content for item in history]
    assert contents == ["u1", "a1", "u2"]


def test_agent_history_getitem():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
        ],
    )

    assert history[0].content == "u1"
    assert history[1].content == "a1"
    assert history[-1].content == "a1"


def test_agent_history_replace_and_dump():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
        ],
    )

    new_items = [
        GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "new1")),
        GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "new2")),
    ]
    history.replace(new_items)

    assert len(history) == 2
    dumped = history.dump()
    assert len(dumped) == 2
    assert dumped[0].content == "new1"
    assert dumped[1].content == "new2"


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
            GtAgentHistory.from_openai_message(
                1, 0,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ),
            GtAgentHistory.from_openai_message(1, 1, assistant_msg),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.tool_result("call_123", '{"ok": true}')),
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
            GtAgentHistory.from_openai_message(
                1, 0,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(
                1, 2,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "done"),
                tags=[AgentHistoryTag.ROOM_TURN_FINISH],
            ),
            GtAgentHistory.from_openai_message(
                1, 3,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ),
        ],
    )

    assert history.has_active_turn() is True
    assert history.get_current_turn_start_index() == 3


# ─── Compact 相关方法 ────────────────────────────────────


def test_build_infer_messages_without_compact_returns_all():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
        ],
    )
    msgs = history.build_infer_messages()
    assert [msg.content for msg in msgs] == ["u1", "a1"]


def test_build_infer_messages_with_compact_includes_summary_and_after():
    # 不变量：COMPACT_SUMMARY 在 _items[0]，build_infer_messages 返回所有消息
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(
                1, 0,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
                tags=[AgentHistoryTag.COMPACT_SUMMARY],
            ),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep last")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "next")),
        ],
    )

    msgs = history.build_infer_messages()

    assert [msg.content for msg in msgs] == ["compact summary", "keep last", "next"]


def test_build_infer_messages_without_compact_returns_all():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep last")),
        ],
    )

    msgs = history.build_infer_messages()

    assert [msg.content for msg in msgs] == ["old1", "old2", "keep last"]


def test_build_compact_source_messages_preserves_latest_user_segment():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2")),
            GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a2")),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 2


def test_build_compact_source_messages_excludes_pending_infer():
    pending_infer = GtAgentHistory.from_openai_message(
        1,
        2,
        llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT),
        stage=AgentHistoryStage.INFER,
        status=AgentHistoryStatus.INIT,
    )
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            pending_infer,
        ],
    )

    plan = history.build_compact_plan()

    assert plan.source_messages == []
    assert plan.insert_seq is None


def test_build_compact_source_messages_keeps_tool_call_chain_after_latest_user():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2")),
            GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "tool call")),
            GtAgentHistory.from_openai_message(1, 4, llmApiUtil.OpenAIMessage.tool_result("call_1", '{"ok": true}')),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 2


def test_build_compact_plan_returns_insert_seq_of_latest_user_message():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2")),
            GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a2")),
        ],
    )

    assert history.build_compact_plan().insert_seq == 2


def test_build_infer_messages_excludes_pending_infer_tail():
    pending_infer = GtAgentHistory.from_openai_message(
        1,
        2,
        llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT),
        stage=AgentHistoryStage.INFER,
        status=AgentHistoryStatus.FAILED,
    )
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1")),
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
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2")),
            GtAgentHistory.from_openai_message(
                1, 2,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
                tags=[AgentHistoryTag.COMPACT_SUMMARY],
            ),
            GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep")),
        ],
    )

    history._trim_to_compact_window()

    assert [item.content for item in history] == ["compact summary", "keep"]


@pytest.mark.asyncio
async def test_append_history_message_with_seq_inserts_at_position():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2")),
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.insert_agent_history_message_at_seq",
        AsyncMock(side_effect=lambda item: item),
    ):
        inserted = await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "mid"),
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
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1")),
            GtAgentHistory.from_openai_message(
                1, 1,
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
                tags=[AgentHistoryTag.COMPACT_SUMMARY],
            ),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep1")),
            GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "keep2")),
            GtAgentHistory.from_openai_message(1, 4, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep")),
        ],
    )
    history._trim_to_compact_window()

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    ):
        appended = await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "next"),
        )

    assert appended.seq == 5
    assert [item.seq for item in history] == [1, 2, 3, 4, 5]
