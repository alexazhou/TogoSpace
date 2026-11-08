"""#169：`AgentActivityMeta` token 字段对齐 `OpenAIUsage` 的判据（M2–M6）。

## 改了什么
真值三键改为与上游同名：`prompt_tokens` / `completion_tokens` / `total_tokens`
（原 `final_prompt_tokens` / `final_completion_tokens` / `final_total_tokens`）；
输出侧估算改名为 `estimated_completion_tokens`（原 `current_completion_tokens` ——
那个键里混了「chunk 带回的真值」和「delta 累加的估算」两套语义）；
`current_total_tokens` 是死键（DB 49067 条 LLM_INFER 里 0 条有值），删除。

## 为什么**平铺**而不是嵌套 `metadata.usage = {...}`（M6 守的就是这条）
`update_activity_progress` 的 metadata 合并是浅合并：
```python
merged = dict(current.metadata or {})
merged.update(patch_dict)      # ← 顶层 key 整体替换
```
若 token 放进嵌套 `usage`，流中每次写 `{usage: {...}}` 都会整体替换 usage，
把收尾写的真值键冲掉。平铺天然被浅合并正确处理，零新机制。
**把 schema 改成嵌套，M6 必红**。

## 与既有用例的分工
- 估算累加的算术口径（三路 delta、O(n) 判据、tokenizer 回退）在
  `tests/unit/service/agentService/test_stream_token_estimate.py`（ST2/ST3/ST4/ST5）。
- 「网关不回 usage 时落库不缺 completion token」在
  `tests/integration/test_compact_flow/test_no_usage_estimate.py`（ST10-A/B）。
- 本文件只测「**这次改名引入的**语义」：真值路径写哪三个键、估算路径写哪个键、
  两条路能否在同一行并存且互不覆盖、浅合并不丢键。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.ormService as ormService
from constants import AgentActivityStatus, AgentActivityType, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentActivity import GtAgentActivity
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import agentActivityService, llmService
from service.agentActivityService import AgentActivityMeta
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from tests.base import ServiceTestCase
from util.llmApiUtil import OpenAIMessage, OpenaiApiRole

_CONFIG_PATCH = "util.configUtil.get_app_config"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"

_AGENT_ID = 1
_TEAM_ID = 1


# ── M2：`apply_usage` 写哪三个键 ────────────────────────────────

class TestApplyUsageWritesUsageNames(ServiceTestCase):
    """M2 真值路径：`apply_usage(usage)` ⇒ 三个与 `OpenAIUsage` 同名的键。"""

    def test_m2_apply_usage_uses_openai_usage_names(self):
        meta = AgentActivityMeta()
        meta.apply_usage(SimpleNamespace(prompt_tokens=1200, completion_tokens=200, total_tokens=1400))

        assert meta.to_dict() == {
            "prompt_tokens": 1200,
            "completion_tokens": 200,
            "total_tokens": 1400,
        }
        # 估算键必须没被真值污染：否则前端会把真值显示成估算色。
        assert "estimated_completion_tokens" not in meta.to_dict()
        assert "estimated_prompt_tokens" not in meta.to_dict()

    def test_m2b_apply_usage_none_writes_nothing(self):
        """`usage is None` 时一个 token 键都不写（收尾兜底另走 `estimated_*`）。"""
        meta = AgentActivityMeta()
        meta.apply_usage(None)

        assert meta.to_dict() == {}


# ── M3 / M4：估算路径 ─────────────────────────────────────────

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


def _resp(content="ok", usage=None):
    msg = OpenAIMessage(role=OpenaiApiRole.ASSISTANT, content=content)
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _make_runner_and_history():
    gt_agent = GtAgent(id=_AGENT_ID, team_id=_TEAM_ID, name="MetaBot", role_template_id=1, model="mock-model@svc")
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


async def _run_infer(stream_script, response) -> dict:
    """跑一次 `_infer_to_item`，返回落库后的 metadata。

    `stream_script` 是一个 `on_progress` 驱动的协程函数，用来精确控制每个
    chunk 走真值分支还是估算分支。
    """
    await GtAgentActivity.delete().aio_execute()
    runner, _ = _make_runner_and_history()
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 1

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_STREAM_PATCH, AsyncMock(side_effect=stream_script)),
        patch(_ESTIMATE_PATCH, return_value=1000),
    ):
        await runner._infer_to_item(output_item, tools=[])

    rows = await GtAgentActivity.select().where(
        GtAgentActivity.activity_type == AgentActivityType.LLM_INFER
    ).aio_execute()
    assert len(rows) == 1, f"应只有一条 LLM_INFER，实际 {len(rows)}"
    assert rows[0].status.name == "SUCCEEDED", f"活动未成功收尾：{rows[0].status}"
    return dict(rows[0].metadata or {})


class TestEstimatePathWritesEstimatedKey(ServiceTestCase):
    """M3 估算路径：chunk 全不带 usage ⇒ 只写 `estimated_*`，不写真值键。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def test_m3_no_usage_writes_only_estimated_keys(self):
        async def _script(model, ctx, on_progress=None, on_status_event=None):
            for i in range(3):
                await on_progress(llmService.InferStreamProgress(
                    delta_text="ab", estimated_completion_tokens=(i + 1) * 2,
                ))
            return llmService.InferResult.success(_resp(content="hi"))

        metadata = await _run_infer(_script, _resp(content="hi"))

        assert metadata["estimated_prompt_tokens"] == 1000
        assert metadata["estimated_completion_tokens"] == 6, metadata
        # 真值键一个都不能出现，尤其不得出现 `completion_tokens: 0` 这种假值——
        # 前端按「键在不在」判估算，出现 0 就会把估算显示成真值。
        assert "completion_tokens" not in metadata, metadata
        assert "prompt_tokens" not in metadata, metadata
        assert "total_tokens" not in metadata, metadata


class TestNoUsageFallbackUsesEstimatedKey(ServiceTestCase):
    """M4 `usage is None` 兜底：写 `estimated_completion_tokens`，不是 `completion_tokens`。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def test_m4_fallback_key_is_estimated_and_value_is_stream_final(self):
        # 29 个 delta（非 10 的整数倍）：避免节流写入点与终值蒙对。
        async def _script(model, ctx, on_progress=None, on_status_event=None):
            for i in range(29):
                await on_progress(llmService.InferStreamProgress(
                    delta_text="ab", estimated_completion_tokens=(i + 1) * 2,
                ))
            return llmService.InferResult.success(_resp(content="hi"))

        metadata = await _run_infer(_script, _resp(content="hi"))

        assert metadata["estimated_completion_tokens"] == 58, metadata
        assert "completion_tokens" not in metadata, (
            f"兜底值写进了真值键，前端会把它当 usage 显示成正常色：{metadata}"
        )


# ── M5：两条路并存且互不覆盖 ───────────────────────────────────

class TestTruthAndEstimateCoexist(ServiceTestCase):
    """M5：流中真值分支与估算分支**互不串键**。

    ## 判据落在两个层面（分别对应一个真实失效面）
    - **M5-a 同一 meta 对象上两键并存**：`apply_progress` 先喂真值进度、再喂估算进度
      （以及反序），两个键都留下、值各是各的。这守的是「拆成两个字段而不是共用一个」。
    - **M5-b 估算键绝不吃到真值**：流里既有带回 usage 的 chunk、又有估算 chunk，
      收尾 `usage is None` 时兜底写进 `estimated_completion_tokens` 的必须是**估算末值**，
      不能是那个 usage 真值。这条在「真值 chunk 排在最后」时才会红
      ——`last_completion_tokens` 若记真值，兜底就把真值写进估算键，
      前端于是把真值显示成估算色。所以两种顺序都要跑。

    ## 为什么不assert「DB 里两键并存」
    流中进度是 `persist=False`（只广播不写库，见 `test_stream_token_activity.py` ST1），
    真值 chunk 的值本来就不落库；落库的真值来自收尾 `apply_usage(usage)`。
    把「DB 里 completion_tokens == 50」写成判据是错的，它测的是别的东西。
    """

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def test_m5a_apply_progress_keeps_both_groups_in_either_order(self):
        truth = llmService.InferStreamProgress(delta_text="a", completion_tokens=50, total_tokens=1050)
        estimate = llmService.InferStreamProgress(delta_text="b", estimated_completion_tokens=12)

        forward = AgentActivityMeta()
        forward.apply_progress(truth)
        forward.apply_progress(estimate)
        assert forward.to_dict()["completion_tokens"] == 50
        assert forward.to_dict()["total_tokens"] == 1050
        assert forward.to_dict()["estimated_completion_tokens"] == 12

        backward = AgentActivityMeta()
        backward.apply_progress(estimate)
        backward.apply_progress(truth)
        assert backward.to_dict()["estimated_completion_tokens"] == 12, (
            f"真值进度把估算键覆盖了：{backward.to_dict()}"
        )
        assert backward.to_dict()["completion_tokens"] == 50

    @pytest.mark.parametrize("truth_chunk_first", [True, False])
    async def test_m5b_fallback_estimate_never_takes_truth_value(self, truth_chunk_first):
        async def _script(model, ctx, on_progress=None, on_status_event=None):
            if truth_chunk_first:
                await on_progress(llmService.InferStreamProgress(
                    delta_text="a", completion_tokens=50, total_tokens=1050,
                ))
            for i in range(3):
                await on_progress(llmService.InferStreamProgress(
                    delta_text="b", estimated_completion_tokens=(i + 1) * 4,
                ))
            if not truth_chunk_first:
                await on_progress(llmService.InferStreamProgress(
                    delta_text="a", completion_tokens=50, total_tokens=1050,
                ))
            return llmService.InferResult.success(_resp(content="hi"))  # usage=None ⇒ 走兜底

        metadata = await _run_infer(_script, _resp(content="hi"))

        assert metadata["estimated_completion_tokens"] == 12, (
            f"兜底值不是估算末值，疑似吃到真值 50（truth_first={truth_chunk_first}）：{metadata}"
        )
        assert metadata["estimated_completion_tokens"] != 50
        # 真值是广播态，不落库（既有设计）：真值键不得被估算值顶上
        assert "completion_tokens" not in metadata, (
            f"估算值写进了真值键：{metadata}"
        )


# ── M6：浅合并不丢键（守「平铺而非嵌套」）───────────────────────

class TestShallowMergeKeepsTruthKeys(ServiceTestCase):
    """M6：先写三真值，再 patch 估算键 ⇒ 三真值仍在。

    这条是把 schema 改成嵌套 `usage: {...}` 的变异检验：浅合并下
    `{usage: {...}}` 会整体替换，三真值当场消失。
    """

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def test_m6_estimated_patch_does_not_drop_truth_keys(self):
        await GtAgentActivity.delete().aio_execute()
        activity = await agentActivityService.add_activity(
            gt_agent=SimpleNamespace(id=_AGENT_ID, team_id=_TEAM_ID),
            activity_type=AgentActivityType.LLM_INFER,
            metadata=AgentActivityMeta(model="mock-model@svc"),
        )

        truth = AgentActivityMeta()
        truth.apply_usage(SimpleNamespace(prompt_tokens=1200, completion_tokens=200, total_tokens=1400))
        await agentActivityService.update_activity_progress(activity.id, metadata_patch=truth)

        await agentActivityService.update_activity_progress(
            activity.id,
            metadata_patch=AgentActivityMeta(estimated_completion_tokens=999),
            persist=False,
        )

        fresh = await GtAgentActivity.aio_get_or_none(GtAgentActivity.id == activity.id)
        stored = dict(fresh.metadata or {})
        assert stored["prompt_tokens"] == 1200, stored
        assert stored["completion_tokens"] == 200, stored
        assert stored["total_tokens"] == 1400, stored
        # persist=False 的估算值不该落库（只广播）
        assert "estimated_completion_tokens" not in stored, stored
