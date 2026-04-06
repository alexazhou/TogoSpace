"""AgentHistoryStore 集成测试：测试需要真实数据库的异步方法。"""
import pytest

import service.ormService as ormService
from constants import AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag, OpenaiLLMApiRole
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

        item = await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "hello db"),
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )

        assert item.id is not None
        assert item.agent_id == 1
        assert item.content == "hello db"
        assert item.stage == AgentHistoryStage.INPUT
        assert len(history) == 1

    async def test_append_stage_init_creates_placeholder(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=2)

        item = await history.append_stage_init(
            stage=AgentHistoryStage.INFER,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )

        assert item.id is not None
        assert item.stage == AgentHistoryStage.INFER
        assert item.status == AgentHistoryStatus.INIT
        assert item.role == OpenaiLLMApiRole.ASSISTANT

    async def test_finalize_history_item_updates_db(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=3)

        init_item = await history.append_stage_init(stage=AgentHistoryStage.INFER)
        assert init_item.status == AgentHistoryStatus.INIT
        assert init_item.id is not None

        final_msg = llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "response text")
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

        init_item = await history.append_stage_init(stage=AgentHistoryStage.TOOL_RESULT, tool_call_id="call_1")
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
        user_msg = llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "user input")
        await history.append_history_message(
            user_msg,
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )

        # 2. 推理
        infer_item = await history.append_stage_init(stage=AgentHistoryStage.INFER)
        assert infer_item.id is not None
        assistant_msg = llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "assistant response")
        await history.finalize_history_item(infer_item.id, assistant_msg, AgentHistoryStatus.SUCCESS)

        assert len(history) == 2
        messages = history.export_openai_message_list()
        assert len(messages) == 2
        assert messages[0].content == "user input"
        assert messages[1].content == "assistant response"

    async def test_append_history_message_persists_seq_and_tags(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=7)

        item = await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "hello"),
            stage=AgentHistoryStage.INPUT,
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )

        assert item.id is not None
        assert item.agent_id == 7
        assert item.seq == 0
        assert item.content == "hello"
        assert item.stage == AgentHistoryStage.INPUT
        assert item.status == AgentHistoryStatus.SUCCESS
        assert item.tags == [AgentHistoryTag.ROOM_TURN_BEGIN]
        assert len(history) == 1
        assert history.last() is not None
        assert history.last().seq == item.seq

    async def test_assert_infer_ready_accepts_user_tool_and_system(self):
        await self._reset_table()
        allowed_roles = [
            OpenaiLLMApiRole.USER,
            OpenaiLLMApiRole.TOOL,
            OpenaiLLMApiRole.SYSTEM,
        ]

        for index, role in enumerate(allowed_roles):
            await GtAgentHistory.delete().aio_execute()
            history = AgentHistoryStore(agent_id=10 + index)
            message = llmApiUtil.OpenAIMessage.text(role, f"msg-{index}")
            if role == OpenaiLLMApiRole.TOOL:
                message = llmApiUtil.OpenAIMessage.tool_result("tool_1", '{"success": true}')

            await history.append_history_message(message)
            history.assert_infer_ready("test_agent")

    async def test_assert_infer_ready_rejects_assistant_tail(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=20)

        await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "hi"),
            stage=AgentHistoryStage.INFER,
            status=AgentHistoryStatus.SUCCESS,
        )

        with pytest.raises(AssertionError, match="assistant"):
            history.assert_infer_ready("test_agent")

    async def test_assert_infer_ready_accepts_failed_or_init_infer_tail(self):
        await GtAgentHistory.delete().aio_execute()
        history_failed = AgentHistoryStore(agent_id=21)
        await history_failed.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, ""),
            stage=AgentHistoryStage.INFER,
            status=AgentHistoryStatus.FAILED,
            error_message="mock error",
        )
        history_failed.assert_infer_ready("test_agent")

        await GtAgentHistory.delete().aio_execute()
        history_init = AgentHistoryStore(agent_id=22)
        init_item = await history_init.append_stage_init(stage=AgentHistoryStage.INFER)
        history_init.assert_infer_ready("test_agent")

    async def test_unfinished_turn(self):
        await self._reset_table()
        history = AgentHistoryStore(agent_id=30)

        await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "u1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.ASSISTANT, "a1"),
            stage=AgentHistoryStage.INFER,
            status=AgentHistoryStatus.SUCCESS,
        )

        assert history.has_unfinished_turn() is True
        assert history.get_unfinished_turn_start_index() == 0

        await history.append_history_message(
            llmApiUtil.OpenAIMessage.text(OpenaiLLMApiRole.USER, "done"),
            tags=[AgentHistoryTag.ROOM_TURN_FINISH],
        )

        assert history.has_unfinished_turn() is False
        assert history.get_unfinished_turn_start_index() is None
