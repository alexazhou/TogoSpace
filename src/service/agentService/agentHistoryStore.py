from __future__ import annotations

from typing import Iterable, Iterator

from constants import AgentHistoryTag, AgentHistoryStage, AgentHistoryStatus, OpenaiLLMApiRole
from dal.db import gtAgentHistoryManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from util import llmApiUtil


class AgentHistoryStore:
    """Agent 历史消息存储：统一管理历史读写、查询与持久化。"""

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

    def assert_infer_ready(self, agent_label: str) -> None:
        last_item = self.last()
        if (
            last_item is not None
            and last_item.role == llmApiUtil.OpenaiLLMApiRole.ASSISTANT
            and last_item.stage == AgentHistoryStage.INFER
            and last_item.status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED)
        ):
            return

        last_role = self.last_role()
        assert last_role in (
            llmApiUtil.OpenaiLLMApiRole.USER,
            llmApiUtil.OpenaiLLMApiRole.TOOL,
            llmApiUtil.OpenaiLLMApiRole.SYSTEM,
        ), f"[{agent_label}] _infer 前最后一条消息不能是 assistant，当前为: {last_role if last_role else 'empty'}"

    def export_openai_message_list(self) -> list[llmApiUtil.OpenAIMessage]:
        return [item.openai_message for item in self._items]

    async def append_history_message(
        self,
        message: llmApiUtil.OpenAIMessage,
        stage: AgentHistoryStage | None = None,
        status: AgentHistoryStatus | None = None,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> GtAgentHistory:
        """追加消息到历史并持久化到数据库。"""
        item = GtAgentHistory.from_openai_message(
            agent_id=self._agent_id,
            seq=len(self._items),
            message=message,
            stage=stage,
            status=status,
            error_message=error_message,
            tags=tags,
        )
        self._items.append(item)
        saved = await gtAgentHistoryManager.append_agent_history_message(item)
        if saved is not None:
            item.id = saved.id
        return item

    async def append_stage_init(
        self,
        stage: AgentHistoryStage,
        tool_call_id: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> GtAgentHistory:
        init_message = llmApiUtil.OpenAIMessage(
            role=self._infer_role_from_stage(stage),
            tool_call_id=tool_call_id,
        )
        return await self.append_history_message(
            init_message,
            stage=stage,
            status=AgentHistoryStatus.INIT,
            tags=tags,
        )

    async def finalize_history_item(
        self,
        history_item: GtAgentHistory,
        message: llmApiUtil.OpenAIMessage | None,
        status: AgentHistoryStatus,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> None:
        message_json: str | None = None
        if message is not None:
            message_json = message.model_dump_json(exclude_none=True)
            history_item.message_json = message_json
        history_item.status = status
        history_item.error_message = error_message
        if tags is not None:
            history_item.tags = list(tags)

        assert history_item.id is not None, "history row id should not be None after append"
        await gtAgentHistoryManager.update_agent_history_by_id(
            history_id=history_item.id,
            message_json=message_json,
            status=status,
            error_message=error_message,
            tags=(history_item.tags if tags is not None else None),
        )

    def get_last_assistant_message(self, start_idx: int = 0) -> llmApiUtil.OpenAIMessage | None:
        recent_history = self._items[start_idx:]
        for item in reversed(recent_history):
            if item.role == llmApiUtil.OpenaiLLMApiRole.ASSISTANT:
                return item.openai_message
        return None

    def find_tool_call_by_id(self, tool_call_id: str, start_idx: int = 0) -> llmApiUtil.OpenAIToolCall | None:
        """在 assistant 消息的 tool_calls 中查找指定 tool_call_id 的调用。"""
        if len(tool_call_id) == 0:
            return None
        for item in reversed(self._items[start_idx:]):
            if item.role != OpenaiLLMApiRole.ASSISTANT or item.tool_calls is None:
                continue
            for tool_call in item.tool_calls:
                if str(tool_call.id or "") == tool_call_id:
                    return tool_call
        return None

    def find_tool_result_by_call_id(self, tool_call_id: str) -> GtAgentHistory | None:
        for item in reversed(self._items):
            if item.role == llmApiUtil.OpenaiLLMApiRole.TOOL and item.tool_call_id == tool_call_id:
                return item
        return None

    def get_unfinished_turn_start_index(self) -> int | None:
        """从尾部向前查找最近一次未完成 turn 的起始 index。"""
        for idx in range(len(self._items) - 1, -1, -1):
            item = self._items[idx]
            if AgentHistoryTag.ROOM_TURN_FINISH in item.tags:
                return None
            if AgentHistoryTag.ROOM_TURN_BEGIN in item.tags:
                return idx
        return None

    def has_unfinished_turn(self) -> bool:
        return self.get_unfinished_turn_start_index() is not None

    @staticmethod
    def _infer_role_from_stage(stage: AgentHistoryStage) -> llmApiUtil.OpenaiLLMApiRole:
        if stage == AgentHistoryStage.INPUT:
            return llmApiUtil.OpenaiLLMApiRole.USER
        if stage == AgentHistoryStage.INFER:
            return llmApiUtil.OpenaiLLMApiRole.ASSISTANT
        if stage == AgentHistoryStage.TOOL_RESULT:
            return llmApiUtil.OpenaiLLMApiRole.TOOL
        raise ValueError(f"不支持的 history stage: {stage}")
