from __future__ import annotations

from typing import Iterable, Iterator

from constants import AgentHistoryTag, OpenaiLLMApiRole
from model.dbModel.gtAgentHistory import GtAgentHistory
from util import llmApiUtil


class AgentHistory:
    """Agent 历史消息容器：统一管理历史读写与查询。"""

    def __init__(self, agent_id: int, items: Iterable[GtAgentHistory] | None = None):
        self._agent_id = agent_id
        self._items: list[GtAgentHistory] = list(items or [])

    @property
    def agent_id(self) -> int:
        return self._agent_id

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[GtAgentHistory]:
        return iter(self._items)

    def __getitem__(self, index):
        return self._items[index]

    def replace(self, items: Iterable[GtAgentHistory]) -> None:
        self._items = list(items)

    def dump(self) -> list[GtAgentHistory]:
        return list(self._items)

    def last(self) -> GtAgentHistory | None:
        if not self._items:
            return None
        return self._items[-1]

    def last_role(self) -> OpenaiLLMApiRole | None:
        last_item = self.last()
        if last_item is None:
            return None
        return last_item.role

    def assert_infer_ready(self, agent_key: str) -> None:
        last_role = self.last_role()
        assert last_role in (
            llmApiUtil.OpenaiLLMApiRole.USER,
            llmApiUtil.OpenaiLLMApiRole.TOOL,
            llmApiUtil.OpenaiLLMApiRole.SYSTEM,
        ), f"[{agent_key}] _infer 前最后一条消息不能是 assistant，当前为: {last_role if last_role else 'empty'}"

    def export_openai_message_list(self) -> list[llmApiUtil.OpenAIMessage]:
        return [item.openai_message for item in self._items]

    def append_message(
        self,
        message: llmApiUtil.OpenAIMessage,
        tags: list[AgentHistoryTag] | None = None,
    ) -> GtAgentHistory:
        item = GtAgentHistory.from_openai_message(
            agent_id=self._agent_id,
            seq=len(self._items),
            message=message,
            tags=tags,
        )
        self._items.append(item)
        return item

    def get_last_assistant_message(self, start_idx: int = 0) -> llmApiUtil.OpenAIMessage | None:
        recent_history = self._items[start_idx:]
        for item in reversed(recent_history):
            if item.role == llmApiUtil.OpenaiLLMApiRole.ASSISTANT:
                return item.openai_message
        return None

    def find_tool_result_by_call_id(self, tool_call_id: str) -> GtAgentHistory | None:
        for item in reversed(self._items):
            if item.role == llmApiUtil.OpenaiLLMApiRole.TOOL and item.tool_call_id == tool_call_id:
                return item
        return None
