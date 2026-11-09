"""流式 token 进度的持久化判据（ST1）与 COMPACT 前后值判据（ST6）。

这两个判据都必须碰**真实数据库**：mock 掉的 activity service 无法回答
「`persist=False` 到底有没有落盘」。
"""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.ormService as ormService
from constants import AgentActivityStatus, AgentActivityType, DriverType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentActivity import GtAgentActivity
from model.dbModel.agentMessage import AgentMessage
from service import agentActivityService, llmService
from service.agentActivityService import AgentActivityMeta
from service.agentService.agentHistoryStore import CompactPlan
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from tests.base import ServiceTestCase
from util import llmApiUtil
from tests.tokenSnapshotStub import make_snapshot, snapshots

_AGENT_ID = 99
_TEAM_ID = 1

_CONFIG_PATCH = "util.configUtil.get_app_config"
_COMPACT_LLM_PATCH = "service.agentService.compact.llmService"
_TOKEN_SNAPSHOT_PATCH = "service.agentService.agentTurnRunner.AgentTurnRunner._token_snapshot"


def _mock_config():
    from util.configUtil.configTypes import (
        LlmContextConfig, LlmModelConfig, LlmProviderConfig, SettingConfig,
    )
    app_config = MagicMock()
    app_config.setting = SettingConfig(
        version="v2",
        language="zh-CN",
        workspace_root="/tmp/test_workspace",
        default_models={"primary": "mock-model@svc", "lite": "mock-model@svc", "vision": "mock-model@svc"},
        llm_providers=[
            LlmProviderConfig(
                name="svc", enable=True, type="openai", api_key="test-key",
                urls={"openai": "http://127.0.0.1:9999/v1/chat/completions"},
                models=[LlmModelConfig(
                    name="mock-model", protocol="openai",
                    context_config=LlmContextConfig(
                        compact_trigger_ratio=0.85,
                        reserve_output_tokens=0,
                        context_window_tokens=400,
                    ),
                )],
            )
        ],
    )
    return app_config


def _make_runner() -> AgentTurnRunner:
    gt_agent = GtAgent(id=_AGENT_ID, team_id=_TEAM_ID, name="StreamBot", role_template_id=1, model="mock-model@svc")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a helpful assistant.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )
    history = MagicMock()
    history.current_session_first_message_id = 1
    history.build_infer_messages = MagicMock(return_value=[
        llmApiUtil.OpenAIMessage(role=llmApiUtil.OpenaiApiRole.USER, content="hello"),
    ])
    history.build_compact_plan = MagicMock(return_value=CompactPlan(
        source_messages=[AgentMessage(role=llmApiUtil.OpenaiApiRole.USER, content="hello")],
        insert_seq=1,
    ))
    history.insert_compact_summary = AsyncMock()
    runner._history = history
    return runner


def _read_metadata_from_new_connection(db_path: str, activity_id: int) -> dict | None:
    """用**独立 sqlite3 连接**读 metadata：绕开 ORM 连接池与任何实例缓存。"""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT metadata FROM agent_activities WHERE id = ?", (activity_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    import json
    return json.loads(row[0]) if row[0] else {}


class _DbCase(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset(self):
        await GtAgentActivity.delete().aio_execute()


class TestPersistFalseDoesNotWrite(_DbCase):
    """ST1：persist=False 只广播不写库，且脏实例不会被后续真实 save 带进库。"""

    async def test_st1_progress_update_leaves_no_trace_in_db(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=GtAgent(id=_AGENT_ID, team_id=_TEAM_ID),
            activity_type=AgentActivityType.LLM_INFER,
            metadata=AgentActivityMeta(model="mock-model@svc", estimated_prompt_tokens=1000),
        )

        for value in (10, 20, 30, 40, 50):
            await agentActivityService.update_activity_progress(
                activity.id,
                metadata_patch=AgentActivityMeta(estimated_completion_tokens=value),
                persist=False,
            )

        # ① 新连接读库：节流中间值一个都不能有
        stored = _read_metadata_from_new_connection(self.test_db_path, activity.id)
        assert stored is not None
        assert "estimated_completion_tokens" not in stored, f"persist=False 竟然落盘了：{stored}"
        assert stored["estimated_prompt_tokens"] == 1000

        # ② ORM 新查询也要一致（不是「只有一个连接看得见」的假象）
        fresh = await GtAgentActivity.aio_get_or_none(GtAgentActivity.id == activity.id)
        assert "estimated_completion_tokens" not in (fresh.metadata or {})

    async def test_st1_dirty_instance_does_not_leak_on_later_real_save(self):
        """脏实例风险实证：先 persist=False 写内存值，再做真实收尾更新，库里只能是收尾值。"""
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=GtAgent(id=_AGENT_ID, team_id=_TEAM_ID),
            activity_type=AgentActivityType.LLM_INFER,
            metadata=AgentActivityMeta(model="mock-model@svc", estimated_prompt_tokens=1000),
        )

        await agentActivityService.update_activity_progress(
            activity.id,
            metadata_patch=AgentActivityMeta(estimated_completion_tokens=777),
            persist=False,
        )

        usage = MagicMock()
        usage.prompt_tokens = 1000
        usage.completion_tokens = 88
        usage.total_tokens = 1088
        final_meta = AgentActivityMeta()
        final_meta.apply_usage(usage)
        await agentActivityService.update_activity_progress(
            activity.id, status=AgentActivityStatus.SUCCEEDED, metadata_patch=final_meta,
        )

        stored = _read_metadata_from_new_connection(self.test_db_path, activity.id)
        assert stored["completion_tokens"] == 88
        assert "estimated_completion_tokens" not in stored, (
            f"脏实例把节流中间值带进库了：{stored}"
        )

    async def test_st1_progress_on_missing_activity_raises(self):
        """行不存在时 persist=False 与写库路径一样报错（不静默吞掉）。"""
        await self._reset()
        with pytest.raises(RuntimeError):
            await agentActivityService.update_activity_progress(
                424242, metadata_patch=AgentActivityMeta(estimated_completion_tokens=1), persist=False,
            )


class TestCompactTokenRange(_DbCase):
    """ST6：COMPACT 活动的 before/after 必须是真实估算值，不是占位。"""

    async def _run_compact(self, pre_tokens: int, post_tokens: int) -> dict:
        runner = _make_runner()
        compact_resp = MagicMock()
        compact_resp.choices = [MagicMock()]
        compact_resp.choices[0].message = llmApiUtil.OpenAIMessage(
            role=llmApiUtil.OpenaiApiRole.ASSISTANT, content="压缩摘要",
        )

        with (
            patch(_CONFIG_PATCH, return_value=_mock_config()),
            # compact 模块持有的是整份 llmService 引用，直接换引用才不会与 runner 的 patch 互相覆盖
            patch(_COMPACT_LLM_PATCH, MagicMock(infer_stream=AsyncMock(
                return_value=llmService.InferResult.success(compact_resp),
            ))),
            # 压缩后的估算值由这里定，用来验证「after 跟着估算走而不是写死」
            patch(_TOKEN_SNAPSHOT_PATCH, side_effect=snapshots(post_tokens)),
        ):
            returned = await runner._execute_compact(pre_snapshot=make_snapshot(pre_tokens))

        # 压缩前后都从快照取，返回值也换成了快照本身。
        assert returned.current_tokens == post_tokens
        rows = await GtAgentActivity.select().where(
            (GtAgentActivity.activity_type == AgentActivityType.COMPACT)
            & (GtAgentActivity.agent_id == _AGENT_ID)
        ).aio_execute()
        assert len(rows) == 1
        return rows[0].metadata

    async def test_st6_before_after_recorded_and_after_matches_estimate(self):
        await self._reset()
        metadata = await self._run_compact(pre_tokens=80000, post_tokens=4200)

        assert metadata["context_tokens_before"] == 80000
        assert metadata["context_tokens_after"] == 4200
        assert metadata["context_tokens_before"] > metadata["context_tokens_after"]

    async def test_st6_after_value_follows_estimate_not_hardcoded(self):
        """换一组估算值，after 必须跟着变——排除「写死常量」的假绿。"""
        await self._reset()
        metadata = await self._run_compact(pre_tokens=12345, post_tokens=678)

        assert metadata["context_tokens_before"] == 12345
        assert metadata["context_tokens_after"] == 678
