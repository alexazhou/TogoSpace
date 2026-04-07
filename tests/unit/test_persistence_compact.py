from constants import AgentHistoryTag, OpenaiApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import persistenceService
from util import llmApiUtil


def test_trim_to_latest_compact_keeps_compact_suffix():
    items = [
        GtAgentHistory.from_openai_message(1, 0, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1")),
        GtAgentHistory.from_openai_message(1, 1, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2")),
        GtAgentHistory.from_openai_message(
            1, 2,
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
        ),
        GtAgentHistory.from_openai_message(1, 3, llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep")),
    ]

    trimmed = persistenceService._trim_to_latest_compact(items)

    assert [item.content for item in trimmed] == ["compact summary", "keep"]
