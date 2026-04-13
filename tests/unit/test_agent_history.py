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


def test_agent_history_placeholder_has_no_message_but_keeps_runtime_metadata():
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


def test_build_infer_messages_skips_placeholder_items():
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


@pytest.mark.asyncio
async def test_insert_compact_summary_trims_old_messages():
    # insert_compact_summary 应将旧消息全部截掉，_items 只保留新 summary + 保留尾部
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=2),
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.insert_agent_history_message_at_seq",
        AsyncMock(side_effect=lambda item: item),
    ):
        # 在 seq=2（保留区起点）插入 summary，old1 和 old2 应被截掉
        await history.insert_compact_summary(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
            seq=2,
        )

    assert [item.content for item in history] == ["compact summary", "keep"]


@pytest.mark.asyncio
async def test_repeated_compact_does_not_accumulate_old_messages():
    """连续两次 compact（compress_all）后，_items 只保留最新的 summary，不累积旧消息。

    这是对 _trim_to_compact_window 用"第一个 COMPACT_SUMMARY"定位时的回归测试：
    旧实现在 insert_seq=items[0].seq 场景下，新 summary 被插到 idx=0，
    _trim_to_compact_window 找到新 summary 后执行 _items[0:] = 不截任何东西，
    导致旧 summary + 旧消息全部保留，内存窗口无限膨胀。
    """
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "turn1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "reply1"), seq=1),
        ],
    )

    mock_insert = AsyncMock(side_effect=lambda item: item)
    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.insert_agent_history_message_at_seq",
        mock_insert,
    ):
        # 第一次 compact：compress_all，insert_seq = items[-1].seq + 1 = 2（追加到末尾）
        await history.insert_compact_summary(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "summary1"),
            seq=2,
        )

    # compact 后只剩 summary1
    assert len(list(history)) == 1
    assert list(history)[0].content == "summary1"

    # 模拟 compact 后新增一条消息（seq 紧跟 summary1 之后）
    history._items.append(
        _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "reply2"), seq=3),
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.insert_agent_history_message_at_seq",
        mock_insert,
    ):
        # 第二次 compact：compress_all，insert_seq = items[-1].seq + 1 = 4
        await history.insert_compact_summary(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "summary2"),
            seq=4,
        )

    # 只剩 summary2，不能出现 summary1 或 reply1/reply2
    contents = [item.content for item in history]
    assert contents == ["summary2"], f"旧消息未清除: {contents}"


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
    # 模拟 compact 后状态：_items 已从 COMPACT_SUMMARY 开始
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"), seq=1, tags=[AgentHistoryTag.COMPACT_SUMMARY]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep1"), seq=2),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "keep2"), seq=3),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=4),
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    ):
        appended = await history.append_history_message(
            GtAgentHistory.build(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "next")),
        )

    assert appended.seq == 5
    assert [item.seq for item in history] == [1, 2, 3, 4, 5]


# ─── finalize_cancel_turn 相关测试 ──────────────────────────


@pytest.mark.asyncio
async def test_finalize_cancel_turn_no_active_turn_is_noop():
    """无 active turn 时，finalize_cancel_turn 什么都不做。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "done"), seq=2, tags=[AgentHistoryTag.ROOM_TURN_FINISH]),
        ],
    )
    original_len = len(history)

    await history.finalize_cancel_turn()

    assert len(history) == original_len


@pytest.mark.asyncio
async def test_finalize_cancel_turn_marks_init_items_as_cancelled():
    """场景 A：INIT 占位项应被标记为 CANCELLED。"""
    init_assistant = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT, status=AgentHistoryStatus.INIT)
    init_assistant.agent_id = 1
    init_assistant.seq = 2
    init_assistant.id = 42  # 模拟已持久化

    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1, status=AgentHistoryStatus.SUCCESS),
            init_assistant,
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.update_agent_history_by_id",
        AsyncMock(),
    ), patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    ):
        await history.finalize_cancel_turn()

    # INIT 项应变为 CANCELLED
    assert init_assistant.status == AgentHistoryStatus.CANCELLED
    # 最后一条应是 ROOM_TURN_FINISH
    last = history.last()
    assert AgentHistoryTag.ROOM_TURN_FINISH in last.tags
    assert history.has_active_turn() is False


@pytest.mark.asyncio
async def test_finalize_cancel_turn_supplements_missing_tool_results():
    """场景 B-2：ASSISTANT 声明了 tool_call 但没有对应 TOOL 记录时，应补写 CANCELLED TOOL 记录。"""
    assistant_item = _make_assistant_tool_call_item(
        seq=1, tool_call_ids=["call_1", "call_2"], status=AgentHistoryStatus.SUCCESS,
    )

    # 只有 call_1 有 TOOL 结果，call_2 缺失
    tool_result_item = _make_item(
        llmApiUtil.OpenAIMessage.tool_result("call_1", '{"ok": true}'),
        seq=2, status=AgentHistoryStatus.SUCCESS,
    )

    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            assistant_item,
            tool_result_item,
        ],
    )

    appended_items = []
    async def _track_append(item):
        appended_items.append(item)
        item.seq = len(history) + len(appended_items) - 1
        return item

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=_track_append),
    ), patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.update_agent_history_by_id",
        AsyncMock(),
    ):
        await history.finalize_cancel_turn()

    # 应该补写了 call_2 的 TOOL 记录 + ROOM_TURN_FINISH
    tool_items = [item for item in history if item.role == OpenaiApiRole.TOOL]
    assert len(tool_items) == 2  # call_1 原有 + call_2 补写
    supplemented = [item for item in tool_items if item.tool_call_id == "call_2"]
    assert len(supplemented) == 1
    assert supplemented[0].status == AgentHistoryStatus.CANCELLED

    # turn 应已关闭
    assert history.has_active_turn() is False


@pytest.mark.asyncio
async def test_finalize_cancel_turn_with_init_assistant_having_tool_calls():
    """场景 B-3：ASSISTANT INIT 占位项 + tool_calls，应同时标记 CANCELLED 并补写 TOOL。"""
    # ASSISTANT 处于 INIT 但已有 tool_calls（推理完成但尚未 finalize 即被取消）
    init_assistant = _make_assistant_tool_call_item(
        seq=1, tool_call_ids=["call_x"], status=AgentHistoryStatus.INIT,
    )
    init_assistant.id = 99

    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            init_assistant,
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.update_agent_history_by_id",
        AsyncMock(),
    ), patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    ):
        await history.finalize_cancel_turn()

    # INIT ASSISTANT 应被标记 CANCELLED
    assert init_assistant.status == AgentHistoryStatus.CANCELLED
    # INIT 项 has_message=False，所以不会走补写 TOOL 的逻辑（它的 tool_calls 不通过 openai_message 暴露）
    # 最后一条应是 ROOM_TURN_FINISH
    assert history.has_active_turn() is False


@pytest.mark.asyncio
async def test_finalize_cancel_turn_cancelled_items_excluded_from_infer_messages():
    """CANCELLED 状态的项在 build_infer_messages 时应被跳过（has_message=False 的占位项）。"""
    init_assistant = GtAgentHistory.build_placeholder(role=OpenaiApiRole.ASSISTANT, status=AgentHistoryStatus.INIT)
    init_assistant.agent_id = 1
    init_assistant.seq = 1
    init_assistant.id = 10

    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0, tags=[AgentHistoryTag.ROOM_TURN_BEGIN]),
            init_assistant,
        ],
    )

    with patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.update_agent_history_by_id",
        AsyncMock(),
    ), patch(
        "service.agentService.agentHistoryStore.gtAgentHistoryManager.append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    ):
        await history.finalize_cancel_turn()

    msgs = history.build_infer_messages()
    contents = [msg.content for msg in msgs]
    # 应包含 u1 和 ROOM_TURN_FINISH 文本，但不包含 CANCELLED 占位
    assert "u1" in contents
    assert any("中断" in c for c in contents)  # ROOM_TURN_FINISH 文本
