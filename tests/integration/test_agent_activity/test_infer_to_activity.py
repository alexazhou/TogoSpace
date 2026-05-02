"""集成测试：验证 AgentTurnRunner._infer_to_item() 推理后触发活动记录插入数据库。"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.ormService as ormService
from constants import AgentActivityStatus, AgentActivityType, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentActivity import GtAgentActivity
from service import llmService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from tests.base import ServiceTestCase
from util.llmApiUtil import OpenAIMessage, OpenaiApiRole

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestInferToActivity(ServiceTestCase):
    """验证 AgentTurnRunner._infer_to_item() 推理后触发活动记录插入。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset(self):
        await GtAgentActivity.delete().aio_execute()

    async def test_infer_creates_reasoning_and_chat_reply_in_db(self):
        """推理返回 reasoning_content 和 content 时，数据库中应有对应活动记录。"""
        await self._reset()

        # 构造 runner 和 history mock
        gt_agent = GtAgent(id=1, team_id=1, name="TestBot", role_template_id=1, model="mock-model")
        runner = AgentTurnRunner(
            gt_agent=gt_agent,
            system_prompt="You are a test agent.",
            driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
        )
        runner._current_room = MagicMock()
        runner._current_room.room_id = 1

        history = MagicMock()
        history.is_infer_ready = MagicMock(return_value=True)
        history.build_infer_messages = MagicMock(return_value=[])
        history.get_pending_infer_item = MagicMock(return_value=None)
        history.append_history_init_item = AsyncMock(return_value=MagicMock(id=1))
        history.finalize_history_item = AsyncMock()
        runner._history = history

        # 构造包含 reasoning_content 和 content 的响应
        msg = OpenAIMessage(
            role=OpenaiApiRole.ASSISTANT,
            content="这是直接发言内容",
            reasoning_content="这是思考过程",
            tool_calls=None,
        )
        resp = MagicMock()
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        resp.choices = [choice]
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        resp.usage = usage

        # Mock config
        llm_cfg = MagicMock()
        llm_cfg.context_window_tokens = 32000
        llm_cfg.reserve_output_tokens = 4096
        llm_cfg.compact_trigger_ratio = 0.85
        llm_cfg.model = "mock-model"
        setting = MagicMock()
        setting.current_llm_service = llm_cfg
        app_config = MagicMock()
        app_config.setting = setting

        with (
            patch("service.agentService.agentTurnRunner.configUtil.get_app_config", return_value=app_config),
            patch("service.agentService.agentTurnRunner.llmService.infer_stream", AsyncMock(return_value=llmService.InferResult.success(resp))),
            patch("service.agentService.agentTurnRunner.compact.estimate_tokens", return_value=1000),
        ):
            result = await runner._infer_to_item(MagicMock(id=1), tools=[])

        # 验证返回的消息内容
        assert result.content == "这是直接发言内容"
        assert result.reasoning_content == "这是思考过程"

        # 验证数据库中有 REASONING 和 CHAT_REPLY 活动记录
        reasoning_rows = await GtAgentActivity.select().where(
            GtAgentActivity.agent_id == 1,
            GtAgentActivity.activity_type == AgentActivityType.REASONING,
        ).aio_execute()
        reasoning_list = list(reasoning_rows)
        assert len(reasoning_list) >= 1
        assert reasoning_list[0].detail == "这是思考过程"
        assert reasoning_list[0].status == AgentActivityStatus.SUCCEEDED

        chat_reply_rows = await GtAgentActivity.select().where(
            GtAgentActivity.agent_id == 1,
            GtAgentActivity.activity_type == AgentActivityType.CHAT_REPLY,
        ).aio_execute()
        chat_reply_list = list(chat_reply_rows)
        assert len(chat_reply_list) >= 1
        assert chat_reply_list[0].detail == "这是直接发言内容"
        assert chat_reply_list[0].status == AgentActivityStatus.SUCCEEDED

    async def test_infer_skips_empty_content_in_db(self):
        """推理返回空 reasoning_content 或空 content 时，数据库中不应有对应活动记录。"""
        await self._reset()

        gt_agent = GtAgent(id=2, team_id=1, name="TestBot2", role_template_id=1, model="mock-model")
        runner = AgentTurnRunner(
            gt_agent=gt_agent,
            system_prompt="You are a test agent.",
            driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
        )
        runner._current_room = MagicMock()
        runner._current_room.room_id = 1

        history = MagicMock()
        history.is_infer_ready = MagicMock(return_value=True)
        history.build_infer_messages = MagicMock(return_value=[])
        history.get_pending_infer_item = MagicMock(return_value=None)
        history.append_history_init_item = AsyncMock(return_value=MagicMock(id=1))
        history.finalize_history_item = AsyncMock()
        runner._history = history

        # 空内容响应
        msg = OpenAIMessage(
            role=OpenaiApiRole.ASSISTANT,
            content="",
            reasoning_content="   ",
            tool_calls=None,
        )
        resp = MagicMock()
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        resp.choices = [choice]
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        resp.usage = usage

        llm_cfg = MagicMock()
        llm_cfg.context_window_tokens = 32000
        llm_cfg.reserve_output_tokens = 4096
        llm_cfg.compact_trigger_ratio = 0.85
        llm_cfg.model = "mock-model"
        setting = MagicMock()
        setting.current_llm_service = llm_cfg
        app_config = MagicMock()
        app_config.setting = setting

        with (
            patch("service.agentService.agentTurnRunner.configUtil.get_app_config", return_value=app_config),
            patch("service.agentService.agentTurnRunner.llmService.infer_stream", AsyncMock(return_value=llmService.InferResult.success(resp))),
            patch("service.agentService.agentTurnRunner.compact.estimate_tokens", return_value=1000),
        ):
            await runner._infer_to_item(MagicMock(id=1), tools=[])

        # 验证数据库中没有 REASONING 和 CHAT_REPLY 活动记录
        reasoning_rows = await GtAgentActivity.select().where(
            GtAgentActivity.agent_id == 2,
            GtAgentActivity.activity_type == AgentActivityType.REASONING,
        ).aio_execute()
        assert len(list(reasoning_rows)) == 0

        chat_reply_rows = await GtAgentActivity.select().where(
            GtAgentActivity.agent_id == 2,
            GtAgentActivity.activity_type == AgentActivityType.CHAT_REPLY,
        ).aio_execute()
        assert len(list(chat_reply_rows)) == 0