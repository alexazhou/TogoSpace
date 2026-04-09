"""Compact 端到端集成测试：真实 AgentHistoryStore + DB，mock LLM 响应。

验证 pre-check compact 触发后，history 结构的完整性与正确性。
"""
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock

import pytest

import service.llmService as llmService
import service.ormService as ormService
from constants import (
    AgentHistoryStatus, AgentHistoryTag,
    DriverType, OpenaiApiRole,
)
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from tests.base import ServiceTestCase
from util import llmApiUtil

# ── Mock 配置：故意把阈值设小，方便触发 compact ──

_CONFIG_PATCH = "service.agentService.agentTurnRunner.configUtil.get_app_config"
_INFER_PATCH = "service.agentService.agentTurnRunner.llmService.infer"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"
_ACTIVITY_PATCH = "service.agentService.agentTurnRunner.agentActivityService"


def _mock_activity_service():
    m = MagicMock()
    act = MagicMock()
    act.id = 1
    m.add_activity = AsyncMock(return_value=act)
    m.update_activity_progress = AsyncMock(return_value=act)
    return m

# context_window=500, reserve=100 → hard_limit=400, trigger=floor(400*0.85)=340
_CONTEXT_WINDOW = 500
_RESERVE_OUTPUT = 100
_HARD_LIMIT = _CONTEXT_WINDOW - _RESERVE_OUTPUT   # 400
_TRIGGER = 340


def _mock_config():
    llm_cfg = MagicMock()
    llm_cfg.context_window_tokens = _CONTEXT_WINDOW
    llm_cfg.reserve_output_tokens = _RESERVE_OUTPUT
    llm_cfg.compact_trigger_ratio = 0.85
    llm_cfg.compact_summary_max_tokens = 200
    llm_cfg.model = "mock-model"
    setting = MagicMock()
    setting.current_llm_service = llm_cfg
    app_config = MagicMock()
    app_config.setting = setting
    return app_config


def _make_runner(history: AgentHistoryStore) -> AgentTurnRunner:
    gt_agent = GtAgent(id=99, team_id=1, name="CompactBot", role_template_id=1, model="mock-model")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a helpful assistant.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )
    runner._history = history
    return runner


def _make_mock_response(content: str):
    """构造 mock LLM 响应对象。"""
    msg = llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT, content=content)
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message = msg
    mock_resp.choices = [mock_choice]
    mock_resp.usage = None
    return mock_resp


class TestCompactFlow(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_and_build_history(self, agent_id: int, turns: int = 5) -> AgentHistoryStore:
        """清空表，构建含 N 轮对话的 history。"""
        await GtAgentHistory.delete().aio_execute()
        history = AgentHistoryStore(agent_id=agent_id)

        for i in range(turns):
            user_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, f"用户消息 {i}")
            await history.append_history_message(GtAgentHistory.build(
                user_msg, status=AgentHistoryStatus.SUCCESS,
            ))
            assistant_msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.ASSISTANT, f"助手回复 {i}")
            await history.append_history_message(GtAgentHistory.build(
                assistant_msg, status=AgentHistoryStatus.SUCCESS,
            ))

        # 追加一条新的 user 消息，使 history 处于 infer-ready 状态
        final_user = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "最新的用户输入")
        await history.append_history_message(GtAgentHistory.build(
            final_user, status=AgentHistoryStatus.SUCCESS,
        ))
        return history

    async def test_pre_check_compact_triggers_and_produces_correct_history(self):
        """完整流程：pre-check 触发 compact → 验证 history 结构。

        估算 token 序列：
        1. _infer_to_item 开始时估算 → 超过 trigger → 触发 _execute_compact
        2. _execute_compact 内部不调 estimate（直接 llmService.infer）
        3. compact 完成后 _check_compact 再次估算 → 低于 trigger → 通过
        4. 主流程 _infer_to_item 发起正常 LLM 请求
        """
        history = await self._reset_and_build_history(agent_id=99, turns=5)
        runner = _make_runner(history)

        initial_count = len(history)
        assert initial_count == 11  # 5轮*2 + 1条新用户消息

        compact_summary_resp = _make_mock_response("这是压缩摘要：之前讨论了5轮对话。")
        normal_resp = _make_mock_response("好的，我来回答你的最新问题。")

        # estimate 调用序列：
        # 1st: _infer_to_item 主流程估算 → 超 trigger
        # 2nd: compact 后重新估算 → 低于 trigger
        # 两次都是 _infer_to_item 中的 estimate_tokens 调用
        estimate_calls = iter([_TRIGGER + 50, 100])

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(compact_summary_resp),
            )),
            mock.patch(_INFER_STREAM_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(normal_resp),
            )),
            mock.patch(_ESTIMATE_PATCH, side_effect=estimate_calls),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            output_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
            result = await runner._infer_to_item(output_item, tools=[])

        assert result.content == "好的，我来回答你的最新问题。"

        # ── 验证 history 结构 ──
        # compact 后 trim_to_compact_window 会裁剪旧消息
        # 剩余结构应为：
        #   [COMPACT_SUMMARY(USER), 最新用户输入(USER), INFER→SUCCESS(ASSISTANT)]
        #
        # 关键验证点：

        # 1. history 中应有 COMPACT_SUMMARY 标记的消息
        has_compact_summary = any(AgentHistoryTag.COMPACT_SUMMARY in item.tags for item in history)
        assert has_compact_summary, "history 中应存在 COMPACT_SUMMARY 标记"

        # 2. build_infer_messages 应该只返回 compact 窗口内的消息（不含旧历史）
        infer_messages = history.build_infer_messages()
        # 不应包含旧的对话消息
        old_contents = {f"用户消息 {i}" for i in range(5)} | {f"助手回复 {i}" for i in range(5)}
        for msg in infer_messages:
            assert msg.content not in old_contents, f"infer_messages 不应包含旧消息: {msg.content}"

        # 3. 最后一条消息应该是 assistant 的正常回复
        last = history.last()
        assert last is not None
        assert last.role == OpenaiApiRole.ASSISTANT
        assert last.status == AgentHistoryStatus.SUCCESS
        assert last.content == "好的，我来回答你的最新问题。"

    async def test_compact_inserts_one_summary_message(self):
        """验证 _execute_compact 插入了 1 条 COMPACT_SUMMARY 消息。"""
        history = await self._reset_and_build_history(agent_id=100, turns=3)
        runner = _make_runner(history)

        initial_count = len(history)
        assert initial_count == 7  # 3轮*2 + 1条新用户消息

        compact_resp = _make_mock_response("摘要内容")

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(compact_resp),
            )),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            result = await runner._execute_compact()

        assert result is True

        # trim 后 history 中应有且仅有 1 条 COMPACT_SUMMARY 消息
        compact_items = [item for item in history if AgentHistoryTag.COMPACT_SUMMARY in item.tags]
        assert len(compact_items) == 1, f"应有 1 条 COMPACT_SUMMARY，实际: {len(compact_items)}"

        summary_item = compact_items[0]
        assert summary_item.role == OpenaiApiRole.USER
        # 内容应包含摘要引导语和摘要内容
        assert "以下是之前对话的压缩摘要" in summary_item.content
        assert "摘要内容" in summary_item.content

    async def test_compact_trim_removes_old_messages_from_memory(self):
        """验证 compact 后 trim_to_compact_window 移除了旧消息。"""
        history = await self._reset_and_build_history(agent_id=101, turns=5)
        runner = _make_runner(history)

        assert len(history) == 11

        compact_resp = _make_mock_response("这是摘要")

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(compact_resp),
            )),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            await runner._execute_compact()

        # trim 后，history 长度应远小于原来的 11 条
        assert len(history) < 11, f"trim 后 history 应变短，实际: {len(history)}"

        # 旧消息不应出现在 infer_messages 中
        infer_messages = history.build_infer_messages()
        for msg in infer_messages:
            for i in range(4):  # 前 4 轮的消息不应出现
                assert msg.content != f"用户消息 {i}", f"旧消息不应出现: {msg.content}"
                assert msg.content != f"助手回复 {i}", f"旧消息不应出现: {msg.content}"

    async def test_build_infer_messages_after_compact_contains_summary_and_current_input(self):
        """compact 完成后 build_infer_messages 应包含 COMPACT_SUMMARY 及其之后的消息。"""
        history = await self._reset_and_build_history(agent_id=102, turns=3)
        runner = _make_runner(history)

        compact_resp = _make_mock_response("摘要：3轮对话")

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(compact_resp),
            )),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            await runner._execute_compact()

        infer_messages = history.build_infer_messages()

        # infer_messages 不应包含 compact 指令本身的内容（指令仅在 compact_messages 内部使用）
        for msg in infer_messages:
            assert "因为上下文长度即将超出限制" not in (msg.content or ""), \
                "infer_messages 不应包含 compact 指令"

        # 应包含上下文恢复消息（含摘要内容）
        resume_found = any("摘要" in (msg.content or "") for msg in infer_messages)
        assert resume_found, "infer_messages 应包含上下文恢复消息"

        # 应包含最新的用户输入
        latest_found = any(msg.content == "最新的用户输入" for msg in infer_messages)
        assert latest_found, "infer_messages 应包含最新的用户输入"
