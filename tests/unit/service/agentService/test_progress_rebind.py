"""进度回调的 activity_id 重绑判据（任务单「我最担心的两点」之一）。

overflow retry 会新建一条 LLM_INFER 活动，旧的已置 FAILED。若回调没有重绑，
后续所有流式进度都会被写到那条已失败的活动上——前端表现为「新活动一直 0、
旧活动却在涨」。这里用「progress 落到哪个 activity_id」直接验。

**id 不硬猜**：活动 id 从 `add_activity` 的实际调用里按 activity_type 取回来，
避免「compact 也占一个 id」这类顺序陷阱把判据写错。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentActivityStatus, AgentActivityType, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import llmService
from service.agentService.agentHistoryStore import CompactPlan
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriverConfig
from util.llmApiUtil import OpenAIMessage, OpenaiApiRole

_CONFIG_PATCH = "util.configUtil.get_app_config"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_COMPACT_LLM_PATCH = "service.agentService.compact.llmService"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"
_ACTIVITY_PATCH = "service.agentService.agentTurnRunner.agentActivityService"

# context_window=32000, reserve=4096 -> hard=27904, trigger=floor(27904*0.85)=23718
_TRIGGER_TOKENS = 23718


def _mock_config():
    from util.configUtil.configTypes import (
        LlmContextConfig, LlmModelConfig, LlmProviderConfig, SettingConfig,
    )
    app_config = MagicMock()
    app_config.setting = SettingConfig(
        version="v2", language="zh-CN", workspace_root="/tmp/test_workspace",
        default_models={"primary": "mock-model@svc", "lite": "mock-model@svc", "vision": "mock-model@svc"},
        llm_providers=[LlmProviderConfig(
            name="svc", enable=True, type="openai", api_key="test-key",
            urls={"openai": "http://127.0.0.1:9999/v1/chat/completions"},
            models=[LlmModelConfig(
                name="mock-model", protocol="openai",
                context_config=LlmContextConfig(
                    compact_trigger_ratio=0.85, reserve_output_tokens=4096, context_window_tokens=32000,
                ),
            )],
        )],
    )
    return app_config


def _make_runner_and_history():
    gt_agent = GtAgent(id=1, team_id=1, name="RebindBot", role_template_id=1, model="mock-model@svc")
    runner = AgentTurnRunner(
        gt_agent=gt_agent, system_prompt="sys",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )
    history = MagicMock()
    history.is_infer_ready = MagicMock(return_value=True)
    history.build_infer_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiApiRole.USER, content="hello"),
    ])
    history.build_compact_plan = MagicMock(return_value=CompactPlan(
        source_messages=[OpenAIMessage(role=OpenaiApiRole.USER, content="hello")], insert_seq=1,
    ))
    item = MagicMock(spec=GtAgentHistory)
    item.id = 1
    history.append_history_init_item = AsyncMock(return_value=item)
    history.finalize_history_item = AsyncMock()
    history.append_history_message = AsyncMock(return_value=item)
    history.insert_compact_summary = AsyncMock(return_value=item)
    runner._history = history
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1
    return runner, history


def _activity_service():
    """活动 id 按序发放，同时记录 (id, activity_type) 供断言反查。"""
    svc = MagicMock()
    created: list[tuple[int, object]] = []
    counter = {"n": 0}

    def _add(**kwargs):
        counter["n"] += 1
        act = MagicMock()
        act.id = counter["n"] * 10  # 10, 20, 30, ...
        created.append((act.id, kwargs.get("activity_type")))
        return act

    svc.add_activity = AsyncMock(side_effect=_add)
    svc.update_activity_progress = AsyncMock(return_value=MagicMock())
    svc.created = created
    return svc


def _ids_of_type(svc, activity_type) -> list[int]:
    return [aid for aid, t in svc.created if t == activity_type]


def _resp(content="ok"):
    msg = OpenAIMessage(role=OpenaiApiRole.ASSISTANT, content=content)
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15
    resp.usage = usage
    return resp


def _progress_ids(svc) -> set[int]:
    return {
        c.args[0]
        for c in svc.update_activity_progress.await_args_list
        if c.kwargs.get("persist") is False
    }


@pytest.mark.asyncio
async def test_progress_after_overflow_retry_goes_to_new_activity():
    """overflow 重试后，进度回调必须指向**新**活动 id，不能继续写已 FAILED 的旧活动。"""
    runner, _ = _make_runner_and_history()
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 1
    svc = _activity_service()
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")

    calls = {"n": 0}

    async def _fake_infer_stream(model, ctx, on_progress=None, on_status_event=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return llmService.InferResult.failure(overflow_error)
        for i in range(20):
            await on_progress(llmService.InferStreamProgress(
                delta_text="x", current_completion_tokens=i + 1,
            ))
        return llmService.InferResult.success(_resp("重试成功"))

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(side_effect=_fake_infer_stream)),
        patch(_COMPACT_LLM_PATCH, MagicMock(infer_stream=AsyncMock(
            return_value=llmService.InferResult.success(_resp("摘要")),
        ))),
        patch(_ESTIMATE_PATCH, side_effect=[5000, 5000, 5000]),
        patch(_ACTIVITY_PATCH, svc),
    ):
        await runner._infer_to_item(output_item, tools=[])

    infer_ids = _ids_of_type(svc, AgentActivityType.LLM_INFER)
    assert len(infer_ids) == 2, f"overflow 重试应建两条 LLM_INFER，实际 {infer_ids}"
    old_id, new_id = infer_ids

    assert _progress_ids(svc) == {new_id}, (
        f"进度写到了 {_progress_ids(svc)}，期望只有重试后的新活动 {new_id}"
    )

    # 旧活动必须只收到一次 FAILED，之后不再有任何更新
    old_calls = [c for c in svc.update_activity_progress.await_args_list if c.args[0] == old_id]
    assert len(old_calls) == 1, f"旧活动被更新了 {len(old_calls)} 次（应只有一次 FAILED）"
    assert old_calls[0].kwargs.get("status") == AgentActivityStatus.FAILED


@pytest.mark.asyncio
async def test_status_event_after_overflow_retry_goes_to_new_activity():
    """`_on_status_event` 也读外层的 activity_id：重绑后事件必须落到新活动。

    这条是隐性风险点——`_on_status_event` 在 retry 之前定义，
    若它按值捕获 activity_id，重试后的重试状态就会写回已失败的旧活动。
    """
    runner, _ = _make_runner_and_history()
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 1
    svc = _activity_service()
    overflow_error = Exception("context_length_exceeded: maximum context length is 32000")

    calls = {"n": 0}

    async def _fake_infer_stream(model, ctx, on_progress=None, on_status_event=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return llmService.InferResult.failure(overflow_error)
        await on_status_event(llmService.InferRequestStatusEvent(
            state=llmService.InferRequestStateType.RETRY_SCHEDULED,
            request_id="rid", attempt=1, max_attempts=3,
            retry_delay_seconds=2, error_message="boom",
        ))
        return llmService.InferResult.success(_resp("重试成功"))

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(side_effect=_fake_infer_stream)),
        patch(_COMPACT_LLM_PATCH, MagicMock(infer_stream=AsyncMock(
            return_value=llmService.InferResult.success(_resp("摘要")),
        ))),
        patch(_ESTIMATE_PATCH, side_effect=[5000, 5000, 5000]),
        patch(_ACTIVITY_PATCH, svc),
    ):
        await runner._infer_to_item(output_item, tools=[])

    infer_ids = _ids_of_type(svc, AgentActivityType.LLM_INFER)
    old_id, new_id = infer_ids[0], infer_ids[1]

    retry_calls = [
        c for c in svc.update_activity_progress.await_args_list
        if c.kwargs.get("metadata_patch") is not None
        and getattr(c.kwargs["metadata_patch"], "request_state", None)
    ]
    assert retry_calls, "没有捕获到重试状态更新"
    assert {c.args[0] for c in retry_calls} == {new_id}, (
        f"重试状态写到了 {[c.args[0] for c in retry_calls]}，期望新活动 {new_id}"
    )
    assert old_id not in {c.args[0] for c in retry_calls}


@pytest.mark.asyncio
async def test_compact_progress_goes_to_compact_activity_not_infer():
    """pre-check compact 的进度必须写到 COMPACT 活动，不能写到推理活动。"""
    runner, _ = _make_runner_and_history()
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 1
    svc = _activity_service()

    async def _fake_compact_infer_stream(model, ctx, on_progress=None, on_status_event=None):
        for i in range(20):
            await on_progress(llmService.InferStreamProgress(
                delta_text="s", current_completion_tokens=i + 1,
            ))
        return llmService.InferResult.success(_resp("摘要"))

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(return_value=llmService.InferResult.success(_resp("答")))),
        patch(_COMPACT_LLM_PATCH, MagicMock(infer_stream=AsyncMock(side_effect=_fake_compact_infer_stream))),
        # 首次估算超 trigger -> pre-check 触发 compact；compact 内部再估一次（裁剪后）
        patch(_ESTIMATE_PATCH, side_effect=[_TRIGGER_TOKENS + 100, 300]),
        patch(_ACTIVITY_PATCH, svc),
    ):
        await runner._infer_to_item(output_item, tools=[])

    compact_ids = _ids_of_type(svc, AgentActivityType.COMPACT)
    infer_ids = _ids_of_type(svc, AgentActivityType.LLM_INFER)
    assert len(compact_ids) == 1, f"应有一条 COMPACT 活动，实际 {svc.created}"
    assert len(infer_ids) == 1

    assert _progress_ids(svc) == {compact_ids[0]}, (
        f"compact 进度写到了 {_progress_ids(svc)}，期望 COMPACT 活动 {compact_ids[0]}"
    )
    assert infer_ids[0] not in _progress_ids(svc), "推理活动不该收到 compact 的流式进度"
