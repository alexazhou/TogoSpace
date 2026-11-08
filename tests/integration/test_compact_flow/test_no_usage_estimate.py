"""ST10：网关不回 usage 时，收尾落库的 completion token（真缺陷判据，拆成 A/B 两条）。

背景：`590eb102` 的核心用途是「网关不回 usage 时本地估算 completion tokens」。
估算值经 `persist=False` 只广播不写库，代码注释写明「最终值由收尾更新落库」。
但收尾用的是 `AgentActivityMeta.apply_usage(usage)`，而 `usage is None` 时它
**什么都不写** —— 于是在「网关不回 usage」这个正是估算存在理由的场景里，
库里一条 completion token 都没有。

后果：流进行中前端能看到数字，用户一刷新 / 另一个窗口打开（活动从 DB 读）
就看不到任何输出 token。

⚠️ 判据设计上踩过的坑（务必保留这里的 delta 数选择逻辑）
基线 `bc16953a` 走 persist=True，节流回调会把**中间值**写进库。节流条件是
「每 10 个 chunk 或每 200ms 至多一次」，所以 delta 数恰好是 10 的整数倍时，
最后一次节流写入的值**正好等于终值**，看起来「基线也没问题」。
实测（`evidence/stream155/st10_coincidence.log`，同一份代码只改 delta 数）：
    N=30（终值 60）→ 基线存 60   ← 巧合，会把基线误判成绿
    N=25（终值 50）→ 基线存 40   ← 真相：存的是被节流的中间值
    N=20（终值 40）→ 基线存 40   ← 巧合
因此下面两条判据都只用**非 10 的整数倍**的 delta 数。

两条判据分工（为什么要拆）：
- **ST10-A「键存在性」**：base 有键 → 绿；590eb102 无键 → 红；fix 有键 → 绿。
  这是「**本提交引入**信息丢失」的举证，只问有没有，不问值对不对。
- **ST10-B「值必须等于流末终值」**：base 40 ≠ 50 → 红；590eb102 红；fix 绿。
  这条揭示一个**既有缺陷**（基线留下的是过时中间值，刷新页面看到的可能是旧数），
  不是本提交引入，故只报不修。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.ormService as ormService
from constants import AgentActivityType, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentActivity import GtAgentActivity
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import llmService
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from tests.base import ServiceTestCase
from util.llmApiUtil import OpenAIMessage, OpenaiApiRole

_CONFIG_PATCH = "util.configUtil.get_app_config"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"

# 必须避开 10 的整数倍：见模块 docstring，10 的倍数会被节流中间值蒙对。
_UNALIGNED_DELTA_COUNTS = [25, 37]


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


def _resp_no_usage(content="hello world"):
    """网关不回 usage：response.usage 为 None。"""
    msg = OpenAIMessage(role=OpenaiApiRole.ASSISTANT, content=content)
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = None
    return resp


def _make_runner_and_history():
    gt_agent = GtAgent(id=1, team_id=1, name="NoUsageBot", role_template_id=1, model="mock-model@svc")
    runner = AgentTurnRunner(
        gt_agent=gt_agent, system_prompt="sys",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )
    history = MagicMock()
    history.is_infer_ready = MagicMock(return_value=True)
    history.build_infer_messages = MagicMock(return_value=[
        OpenAIMessage(role=OpenaiApiRole.USER, content="hello"),
    ])
    item = MagicMock(spec=GtAgentHistory)
    item.id = 1
    history.append_history_init_item = AsyncMock(return_value=item)
    history.finalize_history_item = AsyncMock()
    history.append_history_message = AsyncMock(return_value=item)
    runner._history = history
    runner._current_room = MagicMock()
    runner._current_room.room_id = 1
    return runner, history


async def _run_infer_with_deltas(delta_count: int, response) -> dict:
    """跑一次 _infer_to_item，返回落库后的 metadata。"""
    await GtAgentActivity.delete().aio_execute()
    runner, _ = _make_runner_and_history()
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 1

    async def _fake_infer_stream(model, ctx, on_progress=None, on_status_event=None):
        for i in range(delta_count):
            await on_progress(llmService.InferStreamProgress(
                delta_text="ab", estimated_completion_tokens=(i + 1) * 2,
            ))
        return llmService.InferResult.success(response)

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(side_effect=_fake_infer_stream)),
        patch(_ESTIMATE_PATCH, return_value=1000),
    ):
        await runner._infer_to_item(output_item, tools=[])

    rows = await GtAgentActivity.select().where(
        GtAgentActivity.activity_type == AgentActivityType.LLM_INFER
    ).aio_execute()
    assert len(rows) == 1, f"应只有一条 LLM_INFER，实际 {len(rows)}"
    assert rows[0].status.name == "SUCCEEDED", f"活动未成功收尾：{rows[0].status}"
    return dict(rows[0].metadata or {})


class TestNoUsagePersistsEstimate(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    @pytest.mark.parametrize("delta_count", _UNALIGNED_DELTA_COUNTS)
    async def test_st10a_no_usage_leaves_some_completion_token(self, delta_count):
        """ST10-A：网关不回 usage 时，收尾后库里**必须有** completion token（键存在性）。

        这条是「本提交引入信息丢失」的举证：基线有（不论值新旧），`590eb102` 没有。
        """
        metadata = await _run_infer_with_deltas(delta_count, _resp_no_usage())
        stored = metadata.get("estimated_completion_tokens")
        assert stored is not None, (
            f"网关不回 usage 时库里没有任何 completion token（这正是估算存在的场景）。"
            f"delta 数={delta_count}；metadata={metadata}"
        )

    @pytest.mark.parametrize("delta_count", _UNALIGNED_DELTA_COUNTS)
    async def test_st10b_stored_value_equals_stream_final(self, delta_count):
        """ST10-B：落库值必须等于**流末终值**，不能是被节流的中间值。

        这条在基线上也红（N=25 时基线存 40，终值 50）→ 揭示一个**既有缺陷**：
        即使 persist=True，落库的也可能是过时中间值。非本提交引入，故只报不修。
        """
        expected_final = delta_count * 2  # 每个 delta 记 2 token
        metadata = await _run_infer_with_deltas(delta_count, _resp_no_usage())
        stored = metadata.get("estimated_completion_tokens")
        assert stored == expected_final, (
            f"落库值不是流末终值。delta 数={delta_count}，流末终值={expected_final}，"
            f"库里实际={stored!r}；metadata={metadata}。"
            f"若 stored 接近 10 的整数倍，说明留下的是被节流的中间值而非终值。"
        )

    async def test_st10_with_usage_still_prefers_real_usage(self):
        """反面对照：有 usage 时必须用真实 usage，不能被估算值覆盖。"""
        resp = _resp_no_usage("hello")
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 88
        usage.total_tokens = 188
        resp.usage = usage

        # 25（非 10 的倍数）：确保估算值不会与节流写入点蒙对
        metadata = await _run_infer_with_deltas(25, resp)
        assert metadata["completion_tokens"] == 88, (
            f"有 usage 时应以真实值为准，实际 {metadata}"
        )
        assert metadata["total_tokens"] == 188
        # 有 usage 时不应再有估算兜底值（避免同一行两套口径）
        assert "estimated_completion_tokens" not in metadata, (
            f"有 usage 时不该写估算值，实际 {metadata}"
        )
