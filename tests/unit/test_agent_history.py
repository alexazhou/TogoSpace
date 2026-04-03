import pytest

from constants import AgentHistoryTag, AgentHistoryStage, AgentHistoryStatus, OpenaiLLMApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistroy import AgentHistory
from util import llmApiUtil


def test_agent_history_append_message_persists_seq_and_tags():
    history = AgentHistory(agent_id=7)

    item = history.append_message(
        llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "hello"),
        tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
    )

    assert item.agent_id == 7
    assert item.seq == 0
    assert item.content == "hello"
    assert item.stage == AgentHistoryStage.INPUT
    assert item.status == AgentHistoryStatus.SUCCESS
    assert item.error_message is None
    assert item.tags == [AgentHistoryTag.ROOM_TURN_BEGIN]
    assert len(history) == 1
    assert history.last() is not None
    assert history.last().seq == item.seq
    assert history.last().content == item.content


def test_agent_history_last_role_returns_none_for_empty_history():
    history = AgentHistory(agent_id=1)

    assert history.last() is None
    assert history.last_role() is None


def test_agent_history_assert_infer_ready_accepts_user_tool_and_system():
    allowed_roles = [
        OpenaiLLMApiRole.USER,
        OpenaiLLMApiRole.TOOL,
        OpenaiLLMApiRole.SYSTEM,
    ]

    for index, role in enumerate(allowed_roles):
        history = AgentHistory(agent_id=1)
        message = llmApiUtil.OpenAIMessage.text(role, f"msg-{index}")
        if role == OpenaiLLMApiRole.TOOL:
            message = llmApiUtil.OpenAIMessage.tool_result("tool_1", '{"success": true}')

        history.append_message(message)
        history.assert_infer_ready("alice@test_team")


def test_agent_history_assert_infer_ready_rejects_assistant_tail():
    history = AgentHistory(agent_id=1)
    history.append_message(llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "hi"))

    with pytest.raises(AssertionError, match="assistant"):
        history.assert_infer_ready("alice@test_team")


def test_agent_history_export_openai_message_list_round_trips_messages():
    user_msg = llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")
    tool_msg = llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}')
    history = AgentHistory(
        agent_id=2,
        items=[
            GtAgentHistory.from_openai_message(2, 0, user_msg),
            GtAgentHistory.from_openai_message(2, 1, tool_msg),
        ],
    )

    exported = history.export_openai_message_list()

    assert [msg.role for msg in exported] == [OpenaiLLMApiRole.USER, OpenaiLLMApiRole.TOOL]
    assert [msg.content for msg in exported] == ["u1", '{"success": true}']


def test_agent_history_get_last_assistant_message_respects_start_index():
    history = AgentHistory(
        agent_id=3,
        items=[
            GtAgentHistory.from_openai_message(3, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(3, 1, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(3, 2, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u2")),
            GtAgentHistory.from_openai_message(3, 3, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a2")),
        ],
    )

    last_any = history.get_last_assistant_message()
    last_from_two = history.get_last_assistant_message(start_idx=2)

    assert last_any is not None
    assert last_any.content == "a2"
    assert last_from_two is not None
    assert last_from_two.content == "a2"


def test_agent_history_find_tool_result_by_call_id_returns_matching_history_item():
    history = AgentHistory(
        agent_id=4,
        items=[
            GtAgentHistory.from_openai_message(4, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")),
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
        llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u"),
    )
    assistant_item = GtAgentHistory.from_openai_message(
        9,
        1,
        llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a"),
    )
    tool_item = GtAgentHistory.from_openai_message(
        9,
        2,
        llmApiUtil.OpenAIMessage.tool_result("c1", '{"success": true}'),
    )

    assert user_item.stage == AgentHistoryStage.INPUT
    assert assistant_item.stage == AgentHistoryStage.INFER
    assert tool_item.stage == AgentHistoryStage.TOOL_RESULT
