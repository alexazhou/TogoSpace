"""AgentHistoryStore 集成测试：测试需要真实数据库的异步方法。"""
from __future__ import annotations

import pytest

import service.ormService as ormService
from constants import AgentHistoryStatus, AgentHistoryTag, OpenaiApiRole
from dal.db import gtAgentHistoryManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from tests.base import ServiceTestCase
from util import llmApiUtil


class TestAgentHistoryStoreAsync(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_table(self):
        await GtAgentHistory.delete().aio_execute()

    async def test_append_history_message_persists_to_db(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=1)

        item = await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "hello db"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))

        assert item.id is not None
        assert item.agent_id == 1
        assert item.content == "hello db"
        assert item.role == OpenaiApiRole.USER
        assert len(history) == 1

    async def test_append_history_init_item_creates_placeholder(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=2)

        item = await history.append_history_init_item(
            role=OpenaiApiRole.ASSISTANT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )

        assert item.id is not None
        assert item.role == OpenaiApiRole.ASSISTANT
        assert item.message is None
        assert item.has_message is False
        assert item.status == AgentHistoryStatus.INIT

    async def test_finalize_history_item_updates_db(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=3)

        init_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
        assert init_item.status == AgentHistoryStatus.INIT
        assert init_item.id is not None

        final_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "response text")
        await history.finalize_history_item(
            history_id=init_item.id,
            message=final_msg,
            status=AgentHistoryStatus.SUCCESS,
        )

        assert init_item.status == AgentHistoryStatus.SUCCESS
        assert init_item.content == "response text"

        last = history.last()
        assert last is not None
        assert last.status == AgentHistoryStatus.SUCCESS

    async def test_finalize_history_item_records_error(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=4)

        init_item = await history.append_history_init_item(role=OpenaiApiRole.TOOL, tool_call_id="call_1")
        assert init_item.id is not None
        await history.finalize_history_item(
            history_id=init_item.id,
            message=llmApiUtil.OpenAIMessage.tool_result("call_1", '{"error": "failed"}'),
            status=AgentHistoryStatus.FAILED,
            error_message="tool execution error",
        )

        assert init_item.status == AgentHistoryStatus.FAILED
        assert init_item.error_message == "tool execution error"
        assert init_item.tool_call_id == "call_1"

    async def test_full_flow_append_and_finalize(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=5)

        # 1. 用户输入
        user_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "user input")
        await history.append_history_message(GtAgentHistory.build(
            user_msg,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))

        # 2. 推理
        infer_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
        assert infer_item.id is not None
        assistant_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "assistant response")
        await history.finalize_history_item(infer_item.id, assistant_msg, AgentHistoryStatus.SUCCESS)

        assert len(history) == 2
        messages = [item.openai_message for item in history]
        assert len(messages) == 2
        assert messages[0].content == "user input"
        assert messages[1].content == "assistant response"

    async def test_append_history_message_persists_seq_and_tags(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=7)

        item = await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))

        assert item.id is not None
        assert item.agent_id == 7
        assert item.seq == 0
        assert item.content == "hello"
        assert item.role == OpenaiApiRole.USER
        assert item.status == AgentHistoryStatus.SUCCESS
        assert item.tags == [AgentHistoryTag.ROOM_TURN_BEGIN]
        assert len(history) == 1
        assert history.last() is not None
        assert history.last().seq == item.seq

    async def test_is_infer_ready_accepts_user_tool_and_system(self):
        await self._reset_table()
        allowed_roles = [
            OpenaiApiRole.USER,
            OpenaiApiRole.TOOL,
            OpenaiApiRole.SYSTEM,
        ]

        for index, role in enumerate(allowed_roles):
            await GtAgentHistory.delete().aio_execute()
            history = AgentHistoryStore(agent_id=10 + index)
            message = llmApiUtil.OpenAIMessage.text(role, f"msg-{index}")
            if role == OpenaiApiRole.TOOL:
                message = llmApiUtil.OpenAIMessage.tool_result("tool_1", '{"success": true}')

            await history.append_history_message(GtAgentHistory.build(message))
            assert history.is_infer_ready() is True

    async def test_is_infer_ready_rejects_assistant_tail(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=20)

        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "hi"),
            status=AgentHistoryStatus.SUCCESS,
        ))

        assert history.is_infer_ready() is False

    async def test_is_infer_ready_accepts_failed_or_init_infer_tail(self):
        await GtAgentHistory.delete().aio_execute()
        history_failed = AgentHistoryStore(agent_id=21)
        await history_failed.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, ""),
            status=AgentHistoryStatus.FAILED,
            error_message="mock error",
        ))
        assert history_failed.is_infer_ready() is True

        await GtAgentHistory.delete().aio_execute()
        history_init = AgentHistoryStore(agent_id=22)
        await history_init.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
        assert history_init.is_infer_ready() is True

    async def test_unfinished_turn(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=30)

        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"),
            status=AgentHistoryStatus.SUCCESS,
        ))

        assert history.has_active_turn() is True
        assert history.get_current_turn_start_index() == 0

        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "done"),
            tags=[AgentHistoryTag.ROOM_TURN_FINISH],
        ))

        assert history.has_active_turn() is False
        assert history.get_current_turn_start_index() is None


class TestFinalizeCancelTurn(ServiceTestCase):
    """finalize_cancel_turn: 取消 active turn 的历史清理（使用真实数据库）。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_table(self):
        await GtAgentHistory.delete().aio_execute()

    async def test_no_active_turn_is_noop(self):
        """无 active turn 时，finalize_cancel_turn 什么都不做。"""
        await self._reset_table()
        history = AgentHistoryStore(agent_id=100)

        # 创建已完成的 turn
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"),
            status=AgentHistoryStatus.SUCCESS,
        ))
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "done"),
            tags=[AgentHistoryTag.ROOM_TURN_FINISH],
        ))

        original_len = len(history)
        await history.finalize_cancel_turn()

        assert len(history) == original_len
        assert history.has_active_turn() is False

    async def test_marks_init_items_as_cancelled(self):
        """场景 A：INIT 占位项应被标记为 CANCELLED。"""
        await self._reset_table()
        history = AgentHistoryStore(agent_id=101)

        # 创建 active turn
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"),
            status=AgentHistoryStatus.SUCCESS,
        ))
        init_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)

        init_item_id = init_item.id
        assert init_item_id is not None

        await history.finalize_cancel_turn()

        # reload_from_db 替换了内存对象列表，需要从 history 中重新获取
        cancelled_item = next((item for item in history if item.id == init_item_id), None)
        assert cancelled_item is not None
        assert cancelled_item.status == AgentHistoryStatus.CANCELLED
        last = history.last()
        assert last is not None
        assert AgentHistoryTag.ROOM_TURN_FINISH in last.tags
        assert history.has_active_turn() is False

    async def test_unpersisted_init_item_handled_gracefully(self):
        """未持久化的 INIT 占位项（id=None）应被 reload 过滤掉，不导致异常。

        这是修复本问题的核心测试场景：
        - 模拟 CancelledError 中断持久化，内存中有 INIT 占位但数据库没有
        - reload_from_db 会从数据库重新加载，内存中不再有该占位项
        - finalize_cancel_turn 应正常完成，不抛出 RuntimeError
        """
        await self._reset_table()
        history = AgentHistoryStore(agent_id=102)

        # 创建 active turn，并持久化
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))

        # 模拟：内存中有一个未持久化的 INIT 占位项（id=None）
        unpersisted_init = GtAgentHistory.build_placeholder(
            role=OpenaiApiRole.ASSISTANT,
            status=AgentHistoryStatus.INIT,
        )
        unpersisted_init.agent_id = 102
        unpersisted_init.seq = 1
        # 不设置 id，模拟未持久化状态
        history._items.append(unpersisted_init)

        assert len(history) == 2
        assert unpersisted_init.id is None

        # finalize_cancel_turn 会先 reload，未持久化项被过滤
        await history.finalize_cancel_turn()

        # reload 后，内存只有数据库中的记录 + 新追加的 ROOM_TURN_FINISH
        # 未持久化项被丢弃，不导致 RuntimeError
        assert len(history) == 2  # u1 + ROOM_TURN_FINISH
        assert history.has_active_turn() is False  # turn 被 ROOM_TURN_FINISH 关闭

    async def test_cancelled_turn_can_receive_new_messages(self):
        """取消 turn 后，新的消息应能正常处理。

        验证修复后的行为：取消后 agent 不再卡死。
        """
        await self._reset_table()
        history = AgentHistoryStore(agent_id=103)

        # 创建 active turn
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))
        init_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)

        # 取消 turn
        await history.finalize_cancel_turn()

        assert history.has_active_turn() is False

        # 发送新消息，应能正常处理
        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "new message"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))

        assert len(history) >= 2  # u1 + ROOM_TURN_FINISH + new message
        assert history.has_active_turn() is True  # 新 turn 开始


class TestGetAgentHistoryAfterCompact(ServiceTestCase):
    """测试 get_agent_history_after_compact：只加载 COMPACT_SUMMARY 之后的数据。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_table(self):
        await GtAgentHistory.delete().aio_execute()

    async def test_returns_all_when_no_compact(self):
        """没有 COMPACT_SUMMARY 时，返回全部数据。"""
        await self._reset_table()

        # 创建 10 条历史记录，没有 compact
        for i in range(10):
            item = GtAgentHistory.build(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, f"msg_{i}"),
                status=AgentHistoryStatus.SUCCESS,
                agent_id=200,
                seq=i,
            )
            await gtAgentHistoryManager.append_agent_history_message(item)

        items = await gtAgentHistoryManager.get_agent_history_after_compact(agent_id=200)

        assert len(items) == 10
        assert [item.seq for item in items] == list(range(10))

    async def test_returns_only_after_compact_when_exists(self):
        """有 COMPACT_SUMMARY 时，只返回 seq >= COMPACT_SUMMARY.seq 的数据。"""
        await self._reset_table()

        # 创建历史：seq 0-4 是旧数据，seq 5 是 COMPACT_SUMMARY，seq 6-8 是新数据
        for i in range(5):
            item = GtAgentHistory.build(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, f"old_msg_{i}"),
                status=AgentHistoryStatus.SUCCESS,
                agent_id=201,
                seq=i,
            )
            await gtAgentHistoryManager.append_agent_history_message(item)

        # COMPACT_SUMMARY
        compact_item = GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "compact summary"),
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
            agent_id=201,
            seq=5,
        )
        await gtAgentHistoryManager.append_agent_history_message(compact_item)

        # 新数据
        for i in range(6, 9):
            item = GtAgentHistory.build(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, f"new_msg_{i}"),
                status=AgentHistoryStatus.SUCCESS,
                agent_id=201,
                seq=i,
            )
            await gtAgentHistoryManager.append_agent_history_message(item)

        items = await gtAgentHistoryManager.get_agent_history_after_compact(agent_id=201)

        # 应只返回 seq >= 5 的数据（COMPACT_SUMMARY + 新数据）
        assert len(items) == 4  # seq 5, 6, 7, 8
        assert [item.seq for item in items] == [5, 6, 7, 8]

        # 第一条应是 COMPACT_SUMMARY
        assert AgentHistoryTag.COMPACT_SUMMARY in items[0].tags

    async def test_reload_from_db_filters_old_data(self):
        """reload_from_db 只加载 compact 之后的数据到内存。"""
        await self._reset_table()
        history = AgentHistoryStore(agent_id=202)

        # 创建历史：seq 0-4 旧数据，seq 5 COMPACT_SUMMARY
        for i in range(5):
            await history.append_history_message(GtAgentHistory.build(
                llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, f"old_{i}"),
                status=AgentHistoryStatus.SUCCESS,
            ))

        await history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "summary"),
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
        ))

        # 内存中应有 6 条（包含 COMPACT_SUMMARY）
        assert len(history) == 6

        # 模拟：内存中额外添加一条新消息（未持久化）
        unpersisted = GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "unpersisted"),
            status=AgentHistoryStatus.SUCCESS,
            agent_id=202,
            seq=6,
        )
        history._items.append(unpersisted)
        assert len(history) == 7

        # reload_from_db 应过滤掉未持久化的项，只加载数据库中 compact 之后的数据
        await history.reload_from_db()

        assert len(history) == 1  # 只有 COMPACT_SUMMARY (seq=5)
        assert AgentHistoryTag.COMPACT_SUMMARY in history[0].tags
