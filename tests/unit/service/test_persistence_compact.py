from constants import AgentHistoryTag, OpenaiApiRole
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import persistenceService
from util import llmApiUtil


def _make_item(message, *, seq: int = 0, tags=None) -> GtAgentHistory:
    """测试辅助函数。OpenAIMessage 先经 AgentMessage.from_openai() 转换再构建。"""
    agent_msg = message if isinstance(message, AgentMessage) else AgentMessage.from_openai(message)
    item = GtAgentHistory.build(agent_msg, tags=tags)
    item.agent_id = 1
    item.seq = seq
    return item


def test_trim_to_latest_compact_keeps_compact_suffix():
    items = [
        _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "old1"), seq=0),
        _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "old2"), seq=1),
        _make_item(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
            seq=2,
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
        ),
        _make_item(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "keep"), seq=3),
    ]

    trimmed = persistenceService._trim_to_latest_compact(items)

    assert [item.content for item in trimmed] == ["compact summary", "keep"]