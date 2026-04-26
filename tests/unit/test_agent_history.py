"""AgentHistoryStore 单元测试：测试纯内存操作（不依赖数据库）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from constants import AgentHistoryTag, AgentHistoryStatus, OpenaiApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from util import llmApiUtil


# ─── 测试辅助函数 ──────────────────────────────────────────


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


def _make_assistant_tool_call_item(
    *,
    seq: int,
    tool_call_ids: list[str],
    content: str = "",
    agent_id: int = 1,
    status: AgentHistoryStatus | None = None,
    tags: list[AgentHistoryTag] | None = None,
) -> GtAgentHistory:
    tool_calls = [
        llmApiUtil.OpenAIToolCall.model_validate({
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": f"tool_{index}",
                "arguments": "{}",
            },
        })
        for index, tool_call_id in enumerate(tool_call_ids, start=1)
    ]
    message = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content=content,
        tool_calls=tool_calls,
    )
    return _make_item(message, agent_id=agent_id, seq=seq, status=status, tags=tags)


_MOCK_UPDATE = "service.agentService.agentHistoryStore.gtAgentHistoryManager.update_agent_history_by_id"
_MOCK_APPEND = "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message"
_MOCK_INSERT_AT_SEQ = "service.agentService.agentHistoryStore.gtAgentHistoryManager.insert_agent_history_message_at_seq"


# ─── 基础容器操作 ────────────────────────────────────────────


class TestHistoryBasicOps:
    """__len__, __iter__, __getitem__, replace, last, _last_role 等容器操作。"""

    def test_last_role_returns_none_for_empty_history(self):
        history = AgentHistoryStore(agent_id=1)
        assert history.last() is None
        assert history._last_role() is None

    def test_openai_message_round_trips(self):
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

    def test_len_and_iter(self):
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

    def test_getitem(self):
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

    def test_replace(self):
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

    def test_placeholder_has_no_message_but_keeps_runtime_metadata(self):
        item = GtAgentHistory.build_placeholder(
            role=OpenaiApiRole.TOOL,
            tool_call_id="call_1",
        )

        assert item.role == OpenaiApiRole.TOOL
        assert item.tool_call_id == "call_1"
        assert item.has_message is False
        assert item.openai_message_or_none is None
        assert item.content is None
        assert item.tool_calls is None


# ─── 查询方法 ────────────────────────────────────────────────


class TestHistoryQuery:
    """get_last_assistant_message, find_tool_result_by_call_id, find_tool_call_by_id, turn 追踪。"""

    def test_get_last_assistant_message_respects_start_index(self):
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

    def test_find_tool_result_by_call_id_returns_matching_history_item(self):
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

    def test_find_tool_call_by_id(self):
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

    def test_unfinished_turn_with_items(self):
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


# ─── build_infer_messages ───────────────────────────────────


class TestBuildInferMessages:
    """build_infer_messages 在不同场景下的行为。"""

    def test_skips_placeholder_items(self):
        history = AgentHistoryStore(
            agent_id=5,
            items=[
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), agent_id=5, seq=0),
                GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT, status=AgentHistoryStatus.INIT),
            ],
        )
        history[1].agent_id = 5
        history[1].seq = 1

        msgs = history.build_infer_messages()

        assert [msg.content for msg in msgs] == ["u1"]

    def test_without_compact_returns_all(self):
        history = AgentHistoryStore(
            agent_id=1,
            items=[
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            ],
        )
        msgs = history.build_infer_messages()
        assert [msg.content for msg in msgs] == ["u1", "a1"]

    def test_with_compact_includes_summary_and_after(self):
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

    def test_without_compact_returns_all_multiple(self):
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

    def test_excludes_pending_infer_tail(self):
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


# ─── Compact 相关方法 ────────────────────────────────────────


class TestCompact:
    """insert_compact_summary, append_history_message 指定 seq 等 compact 相关操作。"""

    @pytest.mark.asyncio
    async def test_insert_compact_summary_trims_old_messages(self):
        history = AgentHistoryStore(
            agent_id=1,
            items=[
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1"), seq=0),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2"), seq=1),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=2),
            ],
        )

        with patch(_MOCK_INSERT_AT_SEQ, AsyncMock(side_effect=lambda item: item)):
            await history.insert_compact_summary(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
                seq=2,
            )

        assert [item.content for item in history] == ["compact summary", "keep"]

    @pytest.mark.asyncio
    async def test_repeated_compact_does_not_accumulate_old_messages(self):
        """连续两次 compact 后，_items 只保留最新的 summary，不累积旧消息。"""
        history = AgentHistoryStore(
            agent_id=1,
            items=[
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "turn1"), seq=0),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "reply1"), seq=1),
            ],
        )

        mock_insert = AsyncMock(side_effect=lambda item: item)
        with patch(_MOCK_INSERT_AT_SEQ, mock_insert):
            await history.insert_compact_summary(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "summary1"),
                seq=2,
            )

        assert len(list(history)) == 1
        assert list(history)[0].content == "summary1"

        history._items.append(
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "reply2"), seq=3),
        )

        with patch(_MOCK_INSERT_AT_SEQ, mock_insert):
            await history.insert_compact_summary(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "summary2"),
                seq=4,
            )

        contents = [item.content for item in history]
        assert contents == ["summary2"], f"旧消息未清除: {contents}"

    @pytest.mark.asyncio
    async def test_append_history_message_with_seq_inserts_at_position(self):
        history = AgentHistoryStore(
            agent_id=1,
            items=[
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=1),
            ],
        )

        with patch(_MOCK_INSERT_AT_SEQ, AsyncMock(side_effect=lambda item: item)):
            inserted = await history.append_history_message(
                GtAgentHistory.build(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "mid")),
                seq=1,
            )

        assert inserted.seq == 1
        assert [item.seq for item in history] == [0, 1, 2]
        assert [item.content for item in history] == ["u1", "mid", "u2"]

    @pytest.mark.asyncio
    async def test_append_history_message_uses_last_seq_after_compact_trim(self):
        history = AgentHistoryStore(
            agent_id=1,
            items=[
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"), seq=1, tags=[AgentHistoryTag.COMPACT_SUMMARY]),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep1"), seq=2),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "keep2"), seq=3),
                _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=4),
            ],
        )

        with patch(_MOCK_APPEND, AsyncMock(side_effect=lambda item: item)):
            appended = await history.append_history_message(
                GtAgentHistory.build(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "next")),
            )

        assert appended.seq == 5
        assert [item.seq for item in history] == [1, 2, 3, 4, 5]


