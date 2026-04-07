from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from constants import AgentHistoryTag, AgentHistoryStatus, OpenaiApiRole
from dal.db import gtAgentHistoryManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.historyUsage import HistoryUsage
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

    def last(self) -> GtAgentHistory | None:
        if not self._items:
            return None
        return self._items[-1]

    def _last_role(self) -> OpenaiApiRole | None:
        last_item = self.last()
        if last_item is None:
            return None
        return last_item.role

    def _next_seq(self) -> int:
        last_item = self.last()
        if last_item is None:
            return 0
        return last_item.seq + 1

    def is_infer_ready(self) -> bool:
        """历史末尾是否处于可发起推理的状态。"""
        if self.get_pending_infer_item() is not None:
            return True
        return self._last_role() in (
            llmApiUtil.OpenaiApiRole.USER,
            llmApiUtil.OpenaiApiRole.TOOL,
            llmApiUtil.OpenaiApiRole.SYSTEM,
        )

    def get_pending_infer_item(self) -> GtAgentHistory | None:
        """返回尾部可复用的 pending infer item；否则返回 None。"""
        last_item = self.last()
        if (
            last_item is not None
            and last_item.role == OpenaiApiRole.ASSISTANT
            and last_item.status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED)
        ):
            return last_item
        return None

    async def append_history_message(
        self,
        item: GtAgentHistory,
        *,
        seq: int | None = None,
    ) -> GtAgentHistory:
        """追加或插入消息到历史并持久化。

        若 seq 为 None，追加到末尾；若 seq 有值，按 seq 插入并后移后续消息。
        """
        target_seq = self._next_seq() if seq is None else seq
        item.agent_id = self._agent_id
        item.seq = target_seq

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
        role: OpenaiApiRole,
        tool_call_id: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> GtAgentHistory:
        init_message = llmApiUtil.OpenAIMessage(
            role=role,
            tool_call_id=tool_call_id,
        )
        item = GtAgentHistory.build(
            init_message,
            status=AgentHistoryStatus.INIT,
            tags=tags,
        )
        return await self.append_history_message(item)

    async def finalize_history_item(
        self,
        history_id: int,
        message: llmApiUtil.OpenAIMessage | None,
        status: AgentHistoryStatus,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
        usage: HistoryUsage | None = None,
    ) -> None:
        """完成 history item：更新内存对象并持久化到数据库。

        tags 参数：若不为 None，写入数据库；若为 None，不更新 tags 字段。
        """
        # 更新内存对象
        for item in self._items:
            if item.id == history_id:
                if message is not None:
                    item.message_json = message.model_dump(mode="json", exclude_none=True)
                item.status = status
                item.error_message = error_message
                if tags is not None:
                    item.tags = list(tags)
                if usage is not None:
                    item.usage = usage
                break

        # 持久化到数据库
        message_json = message.model_dump(mode="json", exclude_none=True) if message is not None else None
        await gtAgentHistoryManager.update_agent_history_by_id(
            history_id=history_id,
            message_json=message_json,
            status=status,
            error_message=error_message,
            tags=list(tags) if tags is not None else None,
            usage=usage,
        )

    def get_last_assistant_message(self, start_idx: int = 0) -> llmApiUtil.OpenAIMessage | None:
        recent_history = self._items[start_idx:]
        for item in reversed(recent_history):
            if item.role == llmApiUtil.OpenaiApiRole.ASSISTANT:
                return item.openai_message
        return None

    def find_tool_call_by_id(self, tool_call_id: str) -> llmApiUtil.OpenAIToolCall | None:
        """在未完成 turn 内查找指定 tool_call_id 的调用。"""
        if not tool_call_id:
            return None
        start_idx = self.get_current_turn_start_index()
        if start_idx is None:
            return None
        for item in reversed(self._items[start_idx:]):
            if item.role != OpenaiApiRole.ASSISTANT or item.tool_calls is None:
                continue
            for tool_call in item.tool_calls:
                if tool_call.id == tool_call_id:
                    return tool_call
        return None

    def find_tool_result_by_call_id(self, tool_call_id: str) -> GtAgentHistory | None:
        for item in reversed(self._items):
            if item.role == llmApiUtil.OpenaiApiRole.TOOL and item.tool_call_id == tool_call_id:
                return item
        return None

    def get_first_pending_tool_call(self) -> llmApiUtil.OpenAIToolCall | None:
        """获取未完成 turn 中第一个未执行的 tool_call。"""
        start_idx = self.get_current_turn_start_index()
        if start_idx is None:
            return None
        last_assistant = self.get_last_assistant_message(start_idx=start_idx)
        if last_assistant is None or not last_assistant.tool_calls:
            return None
        for tc in last_assistant.tool_calls:
            result = self.find_tool_result_by_call_id(tc.id)
            if result is None or result.status == AgentHistoryStatus.INIT:
                return tc
        return None

    def get_current_turn_start_index(self) -> int | None:
        """从尾部向前查找最近一次未完成 turn 的起始 index。"""
        for idx in range(len(self._items) - 1, -1, -1):
            item = self._items[idx]
            if AgentHistoryTag.ROOM_TURN_FINISH in item.tags:
                return None
            if AgentHistoryTag.ROOM_TURN_BEGIN in item.tags:
                return idx
        return None

    def has_active_turn(self) -> bool:
        return self.get_current_turn_start_index() is not None

    # ─── Compact 相关方法 ─────────────────────────────────────

    def build_infer_messages(self) -> list[llmApiUtil.OpenAIMessage]:
        """构造本次 _infer() 真正发给模型的消息列表。"""
        items = list(self._items)
        if self.get_pending_infer_item() is not None:
            items = items[:-1]
        return [item.openai_message for item in items]

    def build_compact_plan(self) -> CompactPlan | None:
        """计算本次 compact 的压缩源与 COMPACT_SUMMARY 插入点。

        压缩区间选取逻辑：
        1. 检查末尾是否为 USER 消息
        2. 若是，跳过连续的 USER 消息（保留最新的用户输入），压缩剩余部分
        3. 若不是，压缩全部消息

        示例：
            [USER: u1, ASSISTANT: a1, USER: u2]
                                   ^-- 跳过 u2
            压缩 [USER: u1, ASSISTANT: a1]

            [USER: u1, ASSISTANT: a1(tool_call), TOOL: r1, USER: u2]
                                                          ^-- 跳过 u2
            压缩 [USER: u1, ASSISTANT: a1(tool_call), TOOL: r1]
            tool call 链自然完整

            [USER: u1, ASSISTANT: a1] → 末尾不是 USER，压缩全部

        返回：
            - source_messages: 待压缩的消息列表
            - insert_seq: COMPACT_SUMMARY 的插入位置
        """
        self._assert_compact_invariant()
        items = list(self._items)
        if self.get_pending_infer_item() is not None:
            items = items[:-1]

        if not items:
            return None

        # 检查末尾是否为 USER
        if items[-1].role != llmApiUtil.OpenaiApiRole.USER:
            # 末尾不是 USER，压缩全部
            return CompactPlan(
                source_messages=[item.openai_message for item in items],
                insert_seq=items[0].seq,
            )

        # 从末尾跳过连续的 USER 消息
        preserve_start_idx = len(items) - 1
        for idx in range(len(items) - 1, -1, -1):
            if items[idx].role == llmApiUtil.OpenaiApiRole.USER:
                preserve_start_idx = idx
            else:
                break

        if preserve_start_idx == 0:
            return None

        return CompactPlan(
            source_messages=[item.openai_message for item in items[:preserve_start_idx]],
            insert_seq=items[preserve_start_idx].seq,
        )

    async def insert_compact_summary(
        self,
        message: llmApiUtil.OpenAIMessage,
        seq: int,
    ) -> GtAgentHistory:
        """插入 COMPACT_SUMMARY 消息并立即裁剪旧消息（原子操作）。

        操作完成后满足不变量：_items[0] 为 COMPACT_SUMMARY。
        """
        item = GtAgentHistory.build(
            message,
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
        )
        inserted = await self.append_history_message(item, seq=seq)
        self._trim_to_compact_window()
        self._assert_compact_invariant()
        return inserted

    def _trim_to_compact_window(self) -> None:
        """内存裁剪：只保留 COMPACT_SUMMARY 及其之后的消息。仅由 insert_compact_summary 调用。"""
        for idx, item in enumerate(self._items):
            if AgentHistoryTag.COMPACT_SUMMARY in item.tags:
                self._items = self._items[idx:]
                return

    def _assert_compact_invariant(self) -> None:
        """断言：COMPACT_SUMMARY（若存在）必须在 _items[0]。"""
        for i, item in enumerate(self._items):
            if AgentHistoryTag.COMPACT_SUMMARY in item.tags:
                assert i == 0, (
                    f"[agent_id={self._agent_id}] compact 不变量违反："
                    f"COMPACT_SUMMARY 在 index={i}，必须在 index=0"
                )
                return
