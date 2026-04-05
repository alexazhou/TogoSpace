"""AgentHistoryStore 单元测试：测试纯内存操作（不依赖数据库）。"""
import pytest

from constants import AgentHistoryTag, AgentHistoryStage, AgentHistoryStatus, OpenaiLLMApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from util import llmApiUtil


def test_agent_history_last_role_returns_none_for_empty_history():
    history = AgentHistoryStore(agent_id=1)

    assert history.last() is None
    assert history.last_role() is None


def test_agent_history_export_openai_message_list_round_trips_messages():
    user_msg = llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")
    tool_msg = llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}')
    history = AgentHistoryStore(
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
    history = AgentHistoryStore(
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
    history = AgentHistoryStore(
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


def test_agent_history_len_and_iter():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(1, 2, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u2")),
        ],
    )

    assert len(history) == 3
    contents = [item.content for item in history]
    assert contents == ["u1", "a1", "u2"]


def test_agent_history_getitem():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1")),
        ],
    )

    assert history[0].content == "u1"
    assert history[1].content == "a1"
    assert history[-1].content == "a1"


def test_agent_history_replace_and_dump():
    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")),
        ],
    )

    new_items = [
        GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "new1")),
        GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "new2")),
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
        role=OpenaiLLMApiRole.ASSISTANT,
        content="",
        tool_calls=[tool_call],
    )

    history = AgentHistoryStore(
        agent_id=1,
        items=[
            GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1")),
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
                llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1"),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ),
            GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1")),
            GtAgentHistory.from_openai_message(
                1, 2,
                llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "done"),
                tags=[AgentHistoryTag.ROOM_TURN_FINISH],
            ),
            GtAgentHistory.from_openai_message(
                1, 3,
                llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u2"),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ),
        ],
    )

    assert history.has_unfinished_turn() is True
    assert history.get_unfinished_turn_start_index() == 3