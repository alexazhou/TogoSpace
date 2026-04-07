from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from constants import AgentHistoryTag, AgentHistoryStage, AgentHistoryStatus, OpenaiApiRole
from dal.db import gtAgentHistoryManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from util import llmApiUtil


@dataclass
class CompactPlan:
    """Compact 边界分析结果。

    用于描述一次 compact 需要压缩哪些消息，以及 `COMPACT_SUMMARY`
    应该插入到哪个 `seq` 位置。
    """

    #: 需要送给 compact 模型进行总结的历史消息。
    source_messages: list[llmApiUtil.OpenAIMessage]
    #: `COMPACT_SUMMARY` 需要插入的目标 `seq`；`None` 表示当前没有可执行的 compact 计划。
    insert_seq: int | None



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

    def last_role(self) -> OpenaiApiRole | None:
        last_item = self.last()
        if last_item is None:
            return None
        return last_item.role

    def next_seq(self) -> int:
        last_item = self.last()
        if last_item is None:
            return 0
        return last_item.seq + 1

    def assert_infer_ready(self, agent_label: str) -> None:
        if self.get_pending_infer_item() is not None:
            return

        last_role = self.last_role()
        assert last_role in (
            llmApiUtil.OpenaiApiRole.USER,
            llmApiUtil.OpenaiApiRole.TOOL,
            llmApiUtil.OpenaiApiRole.SYSTEM,
        ), f"[{agent_label}] _infer 前最后一条消息不能是 assistant，当前为: {last_role if last_role else 'empty'}"

    def export_openai_message_list(self) -> list[llmApiUtil.OpenAIMessage]:
        return [item.openai_message for item in self._items]

    def get_pending_infer_item(self) -> GtAgentHistory | None:
        """返回尾部可复用的 pending infer item；否则返回 None。"""
        last_item = self.last()
        if (
            last_item is not None
            and last_item.stage == AgentHistoryStage.INFER
            and last_item.status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED)
        ):
            return last_item
        return None

    async def append_history_message(
        self,
        message: llmApiUtil.OpenAIMessage,
        *,
        seq: int | None = None,
        stage: AgentHistoryStage | None = None,
        status: AgentHistoryStatus | None = None,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
        usage_json: str | None = None,
    ) -> GtAgentHistory:
        """追加或插入消息到历史并持久化。

        若 seq 为 None，追加到末尾；若 seq 有值，按 seq 插入并后移后续消息。
        """
        target_seq = self.next_seq() if seq is None else seq
        item = GtAgentHistory.from_openai_message(
            agent_id=self._agent_id,
            seq=target_seq,
            message=message,
            stage=stage,
            status=status,
            error_message=error_message,
            tags=tags,
        )
        if usage_json is not None:
            item.usage_json = usage_json

        if seq is None:
            # 追加到末尾
            self._items.append(item)
            saved = await gtAgentHistoryManager.append_agent_history_message(item)
        else:
            # 按 seq 插入
            insert_idx = len(self._items)
            for idx, existing in enumerate(self._items):
                if existing.seq >= seq:
                    insert_idx = idx
                    break
            saved = await gtAgentHistoryManager.insert_agent_history_message_at_seq(item)
            for existing in self._items[insert_idx:]:
                existing.seq += 1
            self._items.insert(insert_idx, item)

        if saved is not None:
            item.id = saved.id
        return item

    async def append_history_init_item(
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
        history_id: int,
        message: llmApiUtil.OpenAIMessage | None,
        status: AgentHistoryStatus,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
        usage_json: str | None = None,
    ) -> None:
        """完成 history item：更新内存对象并持久化到数据库。

        tags 参数：若不为 None，写入数据库；若为 None，不更新 tags 字段。
        """
        # 更新内存对象
        for item in self._items:
            if item.id == history_id:
                if message is not None:
                    item.message_json = message.model_dump_json(exclude_none=True)
                item.status = status
                item.error_message = error_message
                if tags is not None:
                    item.tags = list(tags)
                if usage_json is not None:
                    item.usage_json = usage_json
                break

        # 持久化到数据库
        message_json = message.model_dump_json(exclude_none=True) if message is not None else None
        await gtAgentHistoryManager.update_agent_history_by_id(
            history_id=history_id,
            message_json=message_json,
            status=status,
            error_message=error_message,
            tags=list(tags) if tags is not None else None,
            usage_json=usage_json,
        )

    def get_last_assistant_message(self, start_idx: int = 0) -> llmApiUtil.OpenAIMessage | None:
        recent_history = self._items[start_idx:]
        for item in reversed(recent_history):
            if item.role == llmApiUtil.OpenaiApiRole.ASSISTANT:
                return item.openai_message
        return None

    def find_tool_call_by_id(self, tool_call_id: str, start_idx: int = 0) -> llmApiUtil.OpenAIToolCall | None:
        """在 assistant 消息的 tool_calls 中查找指定 tool_call_id 的调用。"""
        if len(tool_call_id) == 0:
            return None
        for item in reversed(self._items[start_idx:]):
            if item.role != OpenaiApiRole.ASSISTANT or item.tool_calls is None:
                continue
            for tool_call in item.tool_calls:
                if tool_call.id == tool_call_id:
                    return tool_call
        return None

    def find_tool_call_by_id_in_unfinished_turn(self, tool_call_id: str) -> llmApiUtil.OpenAIToolCall | None:
        """在未完成 turn 内查找指定 tool_call_id 的调用。"""
        start_idx = self.get_unfinished_turn_start_index()
        if start_idx is None:
            return None
        return self.find_tool_call_by_id(tool_call_id, start_idx=start_idx)

    def find_tool_result_by_call_id(self, tool_call_id: str) -> GtAgentHistory | None:
        for item in reversed(self._items):
            if item.role == llmApiUtil.OpenaiApiRole.TOOL and item.tool_call_id == tool_call_id:
                return item
        return None

    def has_pending_tool_calls_in_unfinished_turn(self) -> bool:
        """检查未完成 turn 中是否有未执行的工具。"""
        return self.get_first_pending_tool_call_in_unfinished_turn() is not None

    def get_first_pending_tool_call_in_unfinished_turn(self) -> llmApiUtil.OpenAIToolCall | None:
        """获取未完成 turn 中第一个未执行的 tool_call。"""
        last_assistant = self.get_last_assistant_message_in_unfinished_turn()
        if last_assistant is None or not last_assistant.tool_calls:
            return None
        for tc in last_assistant.tool_calls:
            result = self.find_tool_result_by_call_id(tc.id)
            if result is None or result.status == AgentHistoryStatus.INIT:
                return tc
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

    def get_last_assistant_message_in_unfinished_turn(self) -> llmApiUtil.OpenAIMessage | None:
        """获取未完成 turn 内的最后一条 assistant 消息，若无未完成 turn 则返回 None。"""
        start_idx = self.get_unfinished_turn_start_index()
        if start_idx is None:
            return None
        return self.get_last_assistant_message(start_idx=start_idx)

    def has_unfinished_turn(self) -> bool:
        return self.get_unfinished_turn_start_index() is not None

    # ─── Compact 相关方法 ─────────────────────────────────────

    def build_infer_messages(self) -> list[llmApiUtil.OpenAIMessage]:
        """构造本次 _infer() 真正发给模型的消息列表。"""
        return [item.openai_message for item in self._get_window_items(exclude_pending_infer=True)]

    def build_compact_plan(self) -> CompactPlan:
        """计算本次 compact 的压缩源与 COMPACT_SUMMARY 插入点。"""
        items = self._get_window_items(exclude_pending_infer=True)
        preserve_start_idx = self._find_compact_preserve_start_index(items)
        if preserve_start_idx is None or preserve_start_idx <= 0:
            return CompactPlan(source_messages=[], insert_seq=None)

        return CompactPlan(
            source_messages=[item.openai_message for item in items[:preserve_start_idx]],
            insert_seq=items[preserve_start_idx].seq,
        )

    def trim_to_compact_window(self) -> None:
        """内存裁剪：只保留最新 COMPACT_SUMMARY 及其之后的消息。"""
        start = self._find_latest_compact_summary_index()
        if start is not None and start > 0:
            self._items = self._items[start:]

    def get_runtime_window_start_index(self) -> int | None:
        """返回当前运行时 history 窗口在原始列表中的起始下标。"""
        return self._find_latest_compact_summary_index()

    def _get_window_items(self, *, exclude_pending_infer: bool) -> list[GtAgentHistory]:
        compact_idx = self._find_latest_compact_summary_index()
        items = list(self._items[compact_idx:] if compact_idx is not None else self._items)
        if (
            exclude_pending_infer
            and items
            and items[-1].stage == AgentHistoryStage.INFER
            and items[-1].status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED)
        ):
            items = items[:-1]
        return items

    def _find_compact_preserve_start_index(self, visible_items: list[GtAgentHistory]) -> int | None:
        for idx in range(len(visible_items) - 1, -1, -1):
            if visible_items[idx].role == llmApiUtil.OpenaiApiRole.USER:
                return idx
        return None if not visible_items else len(visible_items) - 1

    def _find_latest_compact_summary_index(self) -> int | None:
        for idx in range(len(self._items) - 1, -1, -1):
            if AgentHistoryTag.COMPACT_SUMMARY in self._items[idx].tags:
                return idx
        return None

    @staticmethod
    def _infer_role_from_stage(stage: AgentHistoryStage) -> llmApiUtil.OpenaiApiRole:
        if stage == AgentHistoryStage.INPUT:
            return llmApiUtil.OpenaiApiRole.USER
        if stage == AgentHistoryStage.INFER:
            return llmApiUtil.OpenaiApiRole.ASSISTANT
        if stage == AgentHistoryStage.TOOL_RESULT:
            return llmApiUtil.OpenaiApiRole.TOOL
        raise ValueError(f"不支持的 history stage: {stage}")
