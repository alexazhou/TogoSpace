"""AgentHistoryStore.build_compact_plan() 单元测试。"""
from __future__ import annotations

from constants import AgentHistoryStatus, OpenaiApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from util import llmApiUtil


def _make_item(
    message: llmApiUtil.OpenAIMessage,
    *,
    agent_id: int = 1,
    seq: int = 0,
    status: AgentHistoryStatus | None = None,
) -> GtAgentHistory:
    item = GtAgentHistory.build(message, status=status)
    item.agent_id = agent_id
    item.seq = seq
    return item


def _make_assistant_tool_call_item(
    *,
    seq: int,
    tool_call_ids: list[str],
    content: str = "",
    agent_id: int = 1,
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
    return _make_item(message, agent_id=agent_id, seq=seq)


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
    assert plan.insert_seq == 4  # items[-1].seq + 1，追加到末尾，保留区为空


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
    assert plan.insert_seq == 3  # items[-1].seq + 1，追加到末尾，保留区为空


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
    assert plan.insert_seq == 2  # items[-1].seq + 1，追加到末尾，保留区为空


def test_build_compact_can_include_completed_tool_call_chain_when_trailing_is_user():
    """完整闭合的 tool_call/tool_result 链可以进入 compact source。"""
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


def test_build_compact_excludes_unfinished_trailing_tool_call():
    """末尾 assistant 的未完成 tool_call 不应进入 compact source。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
            _make_assistant_tool_call_item(seq=3, tool_call_ids=["call_1"]),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", "u2"]
    assert plan.insert_seq == 3


def test_build_compact_excludes_unfinished_tool_call_tail_before_last_user():
    """同时存在末尾 user 和未完成 tool_call 时，compact source 只保留更早的稳定消息。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=2),
            _make_assistant_tool_call_item(seq=3, tool_call_ids=["call_1"]),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u3"), seq=4),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", "u2"]
    assert plan.insert_seq == 3


def test_build_compact_excludes_partial_multi_tool_call_tail():
    """多工具调用只完成部分结果时，应整体排除该 assistant 及其尾部结果。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_assistant_tool_call_item(seq=2, tool_call_ids=["call_1", "call_2"]),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", '{"ok": true}'), seq=3),
        ],
    )

    plan = history.build_compact_plan()

    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 2


# ─── 补充：完成/未完成 tool_call 的明确覆盖 ──────────────────────────────────


def test_build_compact_compress_all_with_completed_single_tool_call():
    """末尾是已完成的单工具调用链（assistant tool_calls + 对应 tool result），压缩全部。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_assistant_tool_call_item(seq=2, tool_call_ids=["call_1"]),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", "result1"), seq=3),
        ],
    )

    plan = history.build_compact_plan()

    # 全部已完成，_find_unfinished_tool_tail_start 返回 None，末尾非 USER → compress_all
    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", "", "result1"]
    assert plan.insert_seq == 4  # items[-1].seq + 1


def test_build_compact_compress_all_with_completed_multi_tool_call():
    """末尾是已完成的多工具调用链（所有 tool_call 均有对应结果），压缩全部。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_assistant_tool_call_item(seq=1, tool_call_ids=["call_1", "call_2"]),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", "r1"), seq=2),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_2", "r2"), seq=3),
        ],
    )

    plan = history.build_compact_plan()

    assert len(plan.source_messages) == 4
    assert plan.insert_seq == 4  # items[-1].seq + 1


def test_build_compact_includes_completed_tool_chain_before_trailing_user():
    """末尾有 USER，其前是已完成的 tool_call 链；完成的链进入 source，只保留 USER。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_assistant_tool_call_item(seq=2, tool_call_ids=["call_1"]),
            _make_item(llmApiUtil.OpenAIMessage.tool_result("call_1", "result1"), seq=3),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u2"), seq=4),
        ],
    )

    plan = history.build_compact_plan()

    # 跳过末尾 u2；completed tool chain 不触发 unfinished 保护，进入 source
    assert [msg.content for msg in plan.source_messages] == ["u1", "a1", "", "result1"]
    assert plan.insert_seq == 4  # u2 的 seq


def test_build_compact_unfinished_tool_call_no_results_yet():
    """assistant 发出 tool_call 但尚无任何 tool result（刚发出调用）→ 整体保留。"""
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"), seq=0),
            _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"), seq=1),
            _make_assistant_tool_call_item(seq=2, tool_call_ids=["call_1"]),
        ],
    )

    plan = history.build_compact_plan()

    # 末尾是 ASSISTANT(tool_call)，还没有任何 tool result → unfinished → compress 到 assistant 之前
    assert [msg.content for msg in plan.source_messages] == ["u1", "a1"]
    assert plan.insert_seq == 2  # assistant tool_call 的 seq
