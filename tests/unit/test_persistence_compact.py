from constants import AgentHistoryTag, OpenaiLLMApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import persistenceService
from util import llmApiUtil


def test_trim_to_latest_compact_keeps_compact_suffix():
    items = [
        GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "old1")),
        GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "old2")),
        GtAgentHistory.from_openai_message(
            1, 2,
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "compact cmd"),
            tags=[AgentHistoryTag.COMPACT_CMD],
        ),
        GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "summary")),
        GtAgentHistory.from_openai_message(1, 4, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "context")),
        GtAgentHistory.from_openai_message(1, 5, llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "keep")),
    ]

    trimmed = persistenceService._trim_to_latest_compact(items)

    assert [item.content for item in trimmed] == ["compact cmd", "summary", "context", "keep"]
