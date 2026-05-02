"""_infer_to_item() 活动记录触发单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentActivityType, AgentHistoryStatus, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import CompactPlan
from service import llmService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall, OpenaiApiRole


def _make_mock_response(content="ok", tool_calls=None, usage=None, reasoning_content=None):
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _make_usage(prompt=100, completion=50, total=150):
    usage = MagicMock()
    usage.prompt_tokens = prompt
    usage.completion_tokens = completion
    usage.total_tokens = total
    return usage


def _make_history_item(item_id=1):
    item = MagicMock(spec=GtAgentHistory)
    item.id = item_id
    return item


def _make_runner_and_history():
    gt_agent = GtAgent(id=1, team_id=1, name="TestBot", role_template_id=1, model="mock-model")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    history = MagicMock()
    history.is_infer_ready = MagicMock(return_value=True)
    history.build_infer_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiApiRole.USER, content="hello"),
    ])
    history.build_compact_plan = MagicMock(return_value=CompactPlan(
        source_messages=[OpenAIMessage(role=OpenaiApiRole.USER, content="hello")],
        insert_seq=1,
    ))
    history.append_history_init_item = AsyncMock(return_value=_make_history_item())
    history.finalize_history_item = AsyncMock()
    history.append_history_message = AsyncMock(return_value=_make_history_item(2))
    history.insert_compact_summary = AsyncMock(return_value=_make_history_item(2))
    runner._history = history
    return runner, history


def _mock_config():
    llm_cfg = MagicMock()
    llm_cfg.context_window_tokens = 32000
    llm_cfg.reserve_output_tokens = 4096
    llm_cfg.compact_trigger_ratio = 0.85
    llm_cfg.compact_summary_max_tokens = 2048
    llm_cfg.model = "mock-model"
    setting = MagicMock()
    setting.current_llm_service = llm_cfg
    app_config = MagicMock()
    app_config.setting = setting
    return app_config


def _mock_activity_service():
    """返回一个 mock agentActivityService，add_activity 返回带 id 的 mock。"""
    mock_svc = MagicMock()
    mock_activity = MagicMock()
    mock_activity.id = 1
    mock_svc.add_activity = AsyncMock(return_value=mock_activity)
    mock_svc.update_activity_progress = AsyncMock(return_value=mock_activity)
    return mock_svc


_CONFIG_PATCH = "service.agentService.agentTurnRunner.configUtil.get_app_config"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"
_ACTIVITY_PATCH = "service.agentService.agentTurnRunner.agentActivityService"


@pytest.mark.asyncio
async def test_infer_creates_reasoning_and_chat_reply_activities():
    """推理返回 reasoning_content 和 content 时，应触发 REASONING 和 CHAT_REPLY 活动记录。"""
    runner, history = _make_runner_and_history()
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1

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
    resp.usage = _make_usage()
    output_item = _make_history_item()

    mock_activity_svc = _mock_activity_service()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, mock_activity_svc),
    ):
        result_msg = await runner._infer_to_item(output_item, tools=[])

    assert result_msg.content == "这是直接发言内容"
    assert result_msg.reasoning_content == "这是思考过程"

    # 验证 add_activity 被调用三次：LLM_INFER + REASONING + CHAT_REPLY
    assert mock_activity_svc.add_activity.await_count == 3

    # 检查调用参数中的 activity_type
    call_types = [call.kwargs.get("activity_type") for call in mock_activity_svc.add_activity.call_args_list]
    assert AgentActivityType.LLM_INFER in call_types
    assert AgentActivityType.REASONING in call_types
    assert AgentActivityType.CHAT_REPLY in call_types

    # 验证 detail 内容
    reasoning_call = next(c for c in mock_activity_svc.add_activity.call_args_list
                          if c.kwargs.get("activity_type") == AgentActivityType.REASONING)
    assert reasoning_call.kwargs.get("detail") == "这是思考过程"

    chat_reply_call = next(c for c in mock_activity_svc.add_activity.call_args_list
                           if c.kwargs.get("activity_type") == AgentActivityType.CHAT_REPLY)
    assert chat_reply_call.kwargs.get("detail") == "这是直接发言内容"


@pytest.mark.asyncio
async def test_infer_creates_chat_reply_even_with_tool_calls():
    """推理返回 content 且有 tool_calls 时，仍应触发 CHAT_REPLY 活动记录。"""
    runner, history = _make_runner_and_history()
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1

    # 构造包含 content 和 tool_calls 的响应（无 reasoning_content）
    tool_call = OpenAIToolCall(
        id="tc-123",
        type="function",
        function={"name": "execute_bash", "arguments": '{"command": "ls"}'},
    )
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="执行工具前的说明",
        reasoning_content=None,
        tool_calls=[tool_call],
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls"
    resp.choices = [choice]
    resp.usage = _make_usage()
    output_item = _make_history_item()

    mock_activity_svc = _mock_activity_service()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, mock_activity_svc),
    ):
        result_msg = await runner._infer_to_item(output_item, tools=[])

    assert result_msg.content == "执行工具前的说明"
    assert result_msg.tool_calls is not None

    call_types = [call.kwargs.get("activity_type") for call in mock_activity_svc.add_activity.call_args_list]

    # 应包含 CHAT_REPLY（即使有 tool_calls）
    assert AgentActivityType.CHAT_REPLY in call_types
    # 不应包含 REASONING（reasoning_content 为空）
    assert AgentActivityType.REASONING not in call_types


@pytest.mark.asyncio
async def test_infer_skips_activities_for_empty_content():
    """推理返回空 reasoning_content 或空 content 时，不应触发对应活动记录。"""
    runner, history = _make_runner_and_history()
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1

    # 构造空内容的响应
    msg = OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="",           # 空
        reasoning_content="   ",  # 仅空白
        tool_calls=None,
    )
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = _make_usage()
    output_item = _make_history_item()

    mock_activity_svc = _mock_activity_service()

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(resp))),
        patch(_ESTIMATE_PATCH, return_value=1000),
        patch(_ACTIVITY_PATCH, mock_activity_svc),
    ):
        await runner._infer_to_item(output_item, tools=[])

    call_types = [call.kwargs.get("activity_type") for call in mock_activity_svc.add_activity.call_args_list]

    # 只应有 LLM_INFER，不应包含 REASONING 或 CHAT_REPLY
    assert AgentActivityType.LLM_INFER in call_types
    assert AgentActivityType.REASONING not in call_types
    assert AgentActivityType.CHAT_REPLY not in call_types