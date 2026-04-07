"""AgentHistoryStore 集成测试：测试需要真实数据库的异步方法。"""
from __future__ import annotations

import pytest

import service.ormService as ormService
from constants import AgentHistoryStatus, AgentHistoryTag, OpenaiApiRole
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