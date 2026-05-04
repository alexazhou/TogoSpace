from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, Iterator

from constants import AgentHistoryTag, AgentHistoryStatus, OpenaiApiRole
from dal.db import gtAgentHistoryManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.historyUsage import HistoryUsage
from util import llmApiUtil

logger = logging.getLogger(__name__)


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

    async def reload_from_db(self) -> None:
        """从数据库重新加载历史消息，覆盖内存状态。

        只加载 COMPACT_SUMMARY 之后的数据，避免加载已被压缩的旧数据。
        用于在执行关键操作前同步内存与数据库，确保一致性。
        """
        items = await gtAgentHistoryManager.get_agent_history_after_compact(self._agent_id)
        self._items = list(items)
        logger.info("[reload] agent_id=%d, loaded %d items from db (after compact)", self._agent_id, len(self._items))

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
        item = GtAgentHistory.build_placeholder(
            role=role,
            tool_call_id=tool_call_id,
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
                    item.role = message.role
                    item.tool_call_id = message.tool_call_id
                    item.message = message
                item.status = status
                item.error_message = error_message
                if tags is not None:
                    item.tags = list(tags)
                if usage is not None:
                    item.usage = usage
                break

        # 持久化到数据库
        update_kwargs: dict = {
            "history_id": history_id,
            "status": status,
            "error_message": error_message,
            "usage": usage,
        }
        if tags is not None:
            update_kwargs["tags"] = list(tags)
        if message is not None:
            update_kwargs["role"] = message.role
            update_kwargs["tool_call_id"] = message.tool_call_id
            update_kwargs["message"] = message
        await gtAgentHistoryManager.update_agent_history_by_id(**update_kwargs)

    def get_last_assistant_message(self, start_idx: int = 0) -> llmApiUtil.OpenAIMessage | None:
        recent_history = self._items[start_idx:]
        for item in reversed(recent_history):
            if item.role == llmApiUtil.OpenaiApiRole.ASSISTANT and item.has_message:
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
            if item.role != OpenaiApiRole.ASSISTANT or not item.has_message or item.tool_calls is None:
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
        """获取未完成 turn 中第一个未执行的 tool_call。

        已执行（SUCCESS）、执行失败（FAILED）或已取消（CANCELLED）的 tool_call 不再返回。
        """
        start_idx = self.get_current_turn_start_index()
        if start_idx is None:
            return None
        last_assistant = self.get_last_assistant_message(start_idx=start_idx)
        if last_assistant is None or not last_assistant.tool_calls:
            return None
        for tc in last_assistant.tool_calls:
            result = self.find_tool_result_by_call_id(tc.id)
            # None: 还没创建 TOOL 记录 -> 待执行
            # INIT: TOOL 正在执行 -> 待执行
            # SUCCESS/FAILED/CANCELLED: 已完成（不管结果如何） -> 跳过
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

    def is_safe_for_immediate_insert(self) -> bool:
        """当前 history 状态是否处于安全边界，可以插入即时消息。

        安全条件（当前批次已完成）：
        - 末尾是 USER / SYSTEM 消息
        - 末尾是 ASSISTANT(SUCCESS) 且无 tool_calls（直接回复，未发起工具调用）
        - 末尾是 TOOL(SUCCESS/FAILED/CANCELLED) 且整批 tool_calls 均已收尾
        """
        last_item = self.last()
        if last_item is None:
            return False
        role, status = last_item.role, last_item.status
        if role in (OpenaiApiRole.USER, OpenaiApiRole.SYSTEM):
            return True
        if role == OpenaiApiRole.ASSISTANT and status == AgentHistoryStatus.SUCCESS:
            return not last_item.tool_calls
        if role == OpenaiApiRole.TOOL and status in (
            AgentHistoryStatus.SUCCESS,
            AgentHistoryStatus.FAILED,
            AgentHistoryStatus.CANCELLED,
        ):
            return self.get_first_pending_tool_call() is None
        return False

    async def finalize_cancel_turn(self) -> None:
        """取消当前 active turn：将 INIT 占位填充为 CANCELLED，补写缺失的 TOOL 记录，追加 ROOM_TURN_FINISH。

        处理逻辑参见 V17 技术文档 §3.2。
        """
        # 先从数据库 reload，确保内存与数据库一致
        # 避免 CancelledError 中断持久化导致的内存/数据库不一致问题
        await self.reload_from_db()

        start_idx = self.get_current_turn_start_index()
        if start_idx is None:
            logger.info("[cancel-turn] agent_id=%d, 无 active turn，跳过", self._agent_id)
            return

        cancel_reason = "cancelled by user"
        cancel_result_json = json.dumps({"success": False, "message": "因为对话被用户中断，所以工具调用自动被取消"}, ensure_ascii=False)
        turn_items = self._items[start_idx:]

        # 1. 将所有 INIT 占位填充为 CANCELLED
        # TOOL 角色的 INIT 占位需补写占位 message，保证 tool_call 与 tool_result 的配对合规。
        for item in turn_items:
            if item.status == AgentHistoryStatus.INIT:
                if item.role == OpenaiApiRole.TOOL and item.tool_call_id:
                    cancel_msg = llmApiUtil.OpenAIMessage.tool_result(item.tool_call_id, cancel_result_json)
                else:
                    cancel_msg = None
                await self.finalize_history_item(
                    item.id,
                    message=cancel_msg,
                    status=AgentHistoryStatus.CANCELLED,
                    error_message=cancel_reason,
                )

        # 2. 补写缺失的 TOOL 记录（ASSISTANT 声明了 tool_call 但无对应 TOOL 记录，或记录 message=NULL 的情况）
        for item in turn_items:
            if item.role != OpenaiApiRole.ASSISTANT or not item.has_message:
                continue
            tool_calls = item.tool_calls or []
            for tc in tool_calls:
                existing = self.find_tool_result_by_call_id(tc.id)
                tool_message = llmApiUtil.OpenAIMessage.tool_result(tc.id, cancel_result_json)
                if existing is None:
                    await self.append_history_message(GtAgentHistory.build(
                        tool_message,
                        status=AgentHistoryStatus.CANCELLED,
                        error_message=cancel_reason,
                    ))
                elif existing.has_message is False:
                    # 记录存在但 message=NULL（Step 1 未能补写的兜底）
                    await self.finalize_history_item(
                        existing.id,
                        message=tool_message,
                        status=AgentHistoryStatus.CANCELLED,
                        error_message=cancel_reason,
                    )

        # 3. 追加 ROOM_TURN_FINISH 关闭 active turn
        finish_text = "本轮任务已被操作者中断，请以下一条新消息为起点重新出发。"
        await self.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, finish_text),
            tags=[AgentHistoryTag.ROOM_TURN_FINISH],
        ))

        logger.info("[cancel-turn] agent_id=%d, turn 已关闭", self._agent_id)

    # ─── Compact 相关方法 ─────────────────────────────────────

    def build_infer_messages(self) -> list[llmApiUtil.OpenAIMessage]:
        """构造本次 _infer() 真正发给模型的消息列表。"""
        items = list(self._items)
        if self.get_pending_infer_item() is not None:
            items = items[:-1]
        return [item.openai_message for item in items if item.has_message]

    def build_compact_plan(self) -> CompactPlan | None:
        """计算本次 compact 的压缩源与 COMPACT_SUMMARY 插入点。

        压缩区间选取逻辑：
        1. 若尾部存在 pending infer 占位，先排除占位
        2. 从尾部跳过连续的 USER 消息（保留最新用户输入）
        3. 若保留区之前存在未完成的 tool_call 尾巴，则从该 assistant(tool_call) 起整体保留
        4. 压缩更早的稳定前缀

        示例：
            [USER: u1, ASSISTANT: a1, USER: u2]
                                   ^-- 跳过 u2
            压缩 [USER: u1, ASSISTANT: a1]

            [USER: u1, ASSISTANT: a1(tool_call), TOOL: r1, USER: u2]
                                   ^------------------------^-- 未完成 tool_call 尾巴 + 最新 USER 一并保留
            压缩 [USER: u1]

            [USER: u1, ASSISTANT: a1] → 末尾不是 USER，压缩全部

        返回：
            - source_messages: 待压缩的消息列表
            - insert_seq: COMPACT_SUMMARY 的插入位置
        """
        self._assert_compact_invariant()
        items = list(self._items)
        if self.get_pending_infer_item() is not None:
            items = items[:-1]
        items = [item for item in items if item.has_message]

        if not items:
            return None

        preserve_start_idx = self._calc_compact_preserve_start_idx(items)
        if preserve_start_idx == 0:
            return None

        if preserve_start_idx >= len(items):
            insert_seq = items[-1].seq + 1
            logger.info(
                "[compact-plan] agent_id=%d, total_items=%d, preserve_start_idx=%d(compress_all), "
                "insert_seq=%d, compressed=%d, preserved=0",
                self._agent_id, len(items), preserve_start_idx, insert_seq, len(items),
            )
            return CompactPlan(
                source_messages=[item.openai_message for item in items],
                insert_seq=insert_seq,
            )

        insert_seq = items[preserve_start_idx].seq
        logger.info(
            "[compact-plan] agent_id=%d, total_items=%d, preserve_start_idx=%d, "
            "insert_seq=%d, compressed=%d, preserved=%d",
            self._agent_id, len(items), preserve_start_idx, insert_seq,
            preserve_start_idx, len(items) - preserve_start_idx,
        )
        return CompactPlan(
            source_messages=[item.openai_message for item in items[:preserve_start_idx]],
            insert_seq=insert_seq,
        )

    def _calc_compact_preserve_start_idx(self, items: list[GtAgentHistory]) -> int:
        """计算 compact 时需要保留的尾部起点。

        返回值语义：
        - `0`：没有可压缩消息
        - `len(items)`：全部消息都可压缩
        - 其余：从该 index 开始的尾部消息需要保留
        """
        preserve_start_idx = self._skip_trailing_users(items, len(items))
        unfinished_tail_start = self._find_unfinished_tool_tail_start(items, preserve_start_idx)
        if unfinished_tail_start is not None:
            preserve_start_idx = min(preserve_start_idx, unfinished_tail_start)
        return preserve_start_idx

    @staticmethod
    def _skip_trailing_users(items: list[GtAgentHistory], end_idx: int) -> int:
        """跳过末尾连续 USER，返回需要保留的第一条 USER 的 index。"""
        idx = end_idx
        while idx > 0 and items[idx - 1].role == llmApiUtil.OpenaiApiRole.USER:
            idx -= 1
        return idx

    @staticmethod
    def _find_unfinished_tool_tail_start(
        items: list[GtAgentHistory],
        end_idx: int,
    ) -> int | None:
        """查找尾部未完成 tool_call 链的起点。

        若 `items[:end_idx]` 的末尾存在：
        - `ASSISTANT(tool_calls...)`
        - 后跟 0..n 条对应 `TOOL(tool_result)`
        - 但 assistant 声明的全部 tool_call 尚未在该区间闭合

        则返回该 assistant 的 index；否则返回 None。
        """
        if end_idx <= 0:
            return None

        assistant_idx = end_idx - 1
        while assistant_idx >= 0 and items[assistant_idx].role == llmApiUtil.OpenaiApiRole.TOOL:
            assistant_idx -= 1

        if assistant_idx < 0:
            return None

        assistant_item = items[assistant_idx]
        tool_calls = assistant_item.tool_calls or []
        if assistant_item.role != llmApiUtil.OpenaiApiRole.ASSISTANT or not tool_calls:
            return None

        expected_ids = {tool_call.id for tool_call in tool_calls}
        seen_ids = {
            item.tool_call_id
            for item in items[assistant_idx + 1:end_idx]
            if item.role == llmApiUtil.OpenaiApiRole.TOOL and item.tool_call_id in expected_ids
        }
        if expected_ids.issubset(seen_ids):
            return None
        return assistant_idx

    async def insert_compact_summary(
        self,
        message: llmApiUtil.OpenAIMessage,
        seq: int,
    ) -> GtAgentHistory:
        """插入 COMPACT_SUMMARY 消息并立即裁剪旧消息（原子操作）。

        操作完成后满足不变量：_items[0] 为 COMPACT_SUMMARY。

        逻辑：
        - seq 之前的所有消息（旧前缀）被裁掉
        - seq 及之后的消息（保留尾部）seq 整体 +1，紧跟 summary 之后
        - compress_all 场景下 seq = items[-1].seq + 1，保留区为空，_items = [summary]
        """
        items_before = len(self._items)

        # 找出需要保留的尾部（seq >= insert_seq 的消息）
        preserve_idx = len(self._items)
        for idx, existing in enumerate(self._items):
            if existing.seq >= seq:
                preserve_idx = idx
                break
        preserved_items = self._items[preserve_idx:]

        # 构造新 summary item，直接写库
        item = GtAgentHistory.build(
            message,
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
        )
        item.agent_id = self._agent_id
        item.seq = seq
        saved = await gtAgentHistoryManager.insert_agent_history_message_at_seq(item)
        if saved is not None:
            item.id = saved.id

        # 保留尾部消息 seq 后移一位（为 summary 让位）
        for existing in preserved_items:
            existing.seq += 1

        has_old_cs = any(AgentHistoryTag.COMPACT_SUMMARY in p.tags for p in preserved_items)

        # 原子替换：summary + 保留尾部，旧前缀全部丢弃
        self._items = [item] + preserved_items

        logger.info(
            "[compact-insert] agent_id=%d, insert_seq=%d, items_before=%d, "
            "preserve_idx=%d, preserved=%d, items_after=%d, has_old_cs_in_preserved=%s",
            self._agent_id, seq, items_before,
            preserve_idx, len(preserved_items), len(self._items), has_old_cs,
        )

        self._assert_compact_invariant()
        return item

    def _assert_compact_invariant(self) -> None:
        """断言：COMPACT_SUMMARY（若存在）必须在 _items[0]。"""
        for i, item in enumerate(self._items):
            if AgentHistoryTag.COMPACT_SUMMARY in item.tags:
                assert i == 0, (
                    f"[agent_id={self._agent_id}] compact 不变量违反："
                    f"COMPACT_SUMMARY 在 index={i}，必须在 index=0"
                )
                return
