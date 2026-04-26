"""Agent 活动记录集成测试：DAL CRUD + agentActivityService 核心流程。"""
import asyncio
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import service.ormService as ormService
from constants import AgentActivityStatus, AgentActivityType, MessageBusTopic
from dal.db import gtAgentActivityManager
from model.dbModel.gtAgentActivity import GtAgentActivity
from service import agentActivityService, messageBus
from service.agentActivityService import AgentActivityMeta
from tests.base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def _fake_agent(agent_id: int = 1, team_id: int = 1) -> SimpleNamespace:
    """构造仅含 id/team_id 的最小 agent 对象。"""
    return SimpleNamespace(id=agent_id, team_id=team_id)


class TestAgentActivityDAL(ServiceTestCase):
    """DAL Manager CRUD 测试。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset(self):
        await GtAgentActivity.delete().aio_execute()

    # ── create_activity ──

    async def test_create_activity_returns_record_with_id(self):
        await self._reset()
        item = GtAgentActivity(
            agent_id=1, team_id=1,
            activity_type=AgentActivityType.LLM_INFER,
            status=AgentActivityStatus.STARTED,
            title="推理", detail="test",
            started_at=datetime.now(),
            metadata={"room_id": 10},
        )
        result = await gtAgentActivityManager.create_activity(item)
        assert result.id is not None and result.id > 0
        assert result.agent_id == 1
        assert result.metadata == {"room_id": 10}

    # ── update_activity_by_id ──

    async def test_update_activity_changes_status(self):
        await self._reset()
        item = GtAgentActivity(
            agent_id=2, team_id=1,
            activity_type=AgentActivityType.TOOL_CALL,
            status=AgentActivityStatus.STARTED,
            title="工具", started_at=datetime.now(), metadata={},
        )
        await gtAgentActivityManager.create_activity(item)
        updated = await gtAgentActivityManager.update_activity_by_id(
            item.id, status=AgentActivityStatus.SUCCEEDED,
        )
        assert updated.status == AgentActivityStatus.SUCCEEDED

    async def test_update_activity_rejects_invalid_field(self):
        await self._reset()
        item = GtAgentActivity(
            agent_id=1, team_id=1,
            activity_type=AgentActivityType.AGENT_STATE,
            status=AgentActivityStatus.SUCCEEDED,
            title="状态变更", started_at=datetime.now(), metadata={},
        )
        await gtAgentActivityManager.create_activity(item)
        with pytest.raises(ValueError, match="不允许更新以下字段"):
            await gtAgentActivityManager.update_activity_by_id(item.id, agent_id=999)

    # ── list_agent_activities ──

    async def test_list_agent_activities_returns_correct_agent(self):
        await self._reset()
        for aid in (10, 10, 20):
            await gtAgentActivityManager.create_activity(GtAgentActivity(
                agent_id=aid, team_id=1,
                activity_type=AgentActivityType.LLM_INFER,
                status=AgentActivityStatus.STARTED,
                title="推理", started_at=datetime.now(), metadata={},
            ))
        rows = await gtAgentActivityManager.list_agent_activities(10)
        assert len(rows) == 2
        assert all(r.agent_id == 10 for r in rows)

    # ── list_team_activities ──

    async def test_list_team_activities_returns_correct_team(self):
        await self._reset()
        for tid in (1, 1, 2):
            await gtAgentActivityManager.create_activity(GtAgentActivity(
                agent_id=1, team_id=tid,
                activity_type=AgentActivityType.TOOL_CALL,
                status=AgentActivityStatus.STARTED,
                title="工具", started_at=datetime.now(), metadata={},
            ))
        rows = await gtAgentActivityManager.list_team_activities(1)
        assert len(rows) == 2

    # ── list_activities with room_id filter ──

    async def test_list_activities_filters_by_room_id(self):
        await self._reset()
        await gtAgentActivityManager.create_activity(GtAgentActivity(
            agent_id=1, team_id=1,
            activity_type=AgentActivityType.LLM_INFER,
            status=AgentActivityStatus.STARTED,
            title="推理", started_at=datetime.now(),
            metadata={"room_id": 42},
        ))
        await gtAgentActivityManager.create_activity(GtAgentActivity(
            agent_id=1, team_id=1,
            activity_type=AgentActivityType.LLM_INFER,
            status=AgentActivityStatus.STARTED,
            title="推理", started_at=datetime.now(),
            metadata={"room_id": 99},
        ))
        rows = await gtAgentActivityManager.list_activities(room_id=42)
        assert len(rows) == 1
        assert rows[0].metadata["room_id"] == 42

    async def test_list_activities_with_limit(self):
        await self._reset()
        for _ in range(5):
            await gtAgentActivityManager.create_activity(GtAgentActivity(
                agent_id=1, team_id=1,
                activity_type=AgentActivityType.LLM_INFER,
                status=AgentActivityStatus.STARTED,
                title="推理", started_at=datetime.now(), metadata={},
            ))
        rows = await gtAgentActivityManager.list_activities(limit=3)
        assert len(rows) == 3


class TestAgentActivityService(ServiceTestCase):
    """agentActivityService 核心流程测试。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset(self):
        await GtAgentActivity.delete().aio_execute()

    # ── add_activity：基础创建 ──

    async def test_add_activity_creates_record(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER, title="推理",
        )
        assert activity.id is not None
        assert activity.status == AgentActivityStatus.STARTED
        assert activity.finished_at is None
        assert activity.duration_ms is None

    # ── add_activity：结束态自动补字段 ──

    async def test_add_activity_terminal_status_fills_finished_at(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.AGENT_STATE,
            status=AgentActivityStatus.SUCCEEDED,
        )
        assert activity.finished_at is not None
        assert activity.duration_ms is None

    # ── add_activity：默认 title ──

    async def test_add_activity_default_title(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER,
        )
        assert activity.title == "推理"

    async def test_add_activity_custom_title_overrides_default(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER, title="自定义标题",
        )
        assert activity.title == "自定义标题"

    # ── add_activity：messageBus 广播 ──

    async def test_add_activity_broadcasts_event(self):
        await self._reset()
        received = []
        def listener(msg):
            received.append(msg)

        messageBus.subscribe(MessageBusTopic.AGENT_ACTIVITY_CHANGED, listener)
        try:
            await agentActivityService.add_activity(
                gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER,
            )
            await asyncio.sleep(0.05)
            assert len(received) == 1
            assert "activity" in received[0].payload
        finally:
            messageBus.unsubscribe(MessageBusTopic.AGENT_ACTIVITY_CHANGED, listener)

    # ── update_activity_progress：基础更新 ──

    async def test_update_progress_changes_detail(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER,
        )
        updated = await agentActivityService.update_activity_progress(activity.id, detail="tokens: 50")
        assert updated.detail == "tokens: 50"

    # ── update_activity_progress：结束态自动补 finished_at 和 duration_ms ──

    async def test_update_progress_terminal_fills_time(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER,
        )
        await asyncio.sleep(0.01)
        updated = await agentActivityService.update_activity_progress(activity.id, status=AgentActivityStatus.SUCCEEDED)
        assert updated.finished_at is not None
        assert updated.duration_ms is not None
        assert updated.duration_ms >= 0

    # ── update_activity_progress：metadata 浅合并 ──

    async def test_update_progress_metadata_shallow_merge(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.LLM_INFER,
            metadata=AgentActivityMeta(room_id=5, model="test-model"),
        )
        updated = await agentActivityService.update_activity_progress(activity.id, metadata_patch=AgentActivityMeta(final_prompt_tokens=100, model="override"))
        assert updated.metadata["room_id"] == 5
        assert updated.metadata["model"] == "override"
        assert updated.metadata["final_prompt_tokens"] == 100

    async def test_add_activity_keeps_tool_command_metadata(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.TOOL_CALL,
            metadata=AgentActivityMeta(tool_name="execute_bash", command="cat /tmp/demo.txt"),
        )
        assert activity.metadata["tool_name"] == "execute_bash"
        assert activity.metadata["command"] == "cat /tmp/demo.txt"

    # ── update_activity_progress：FAILED 状态附带 error_message ──

    async def test_update_progress_failed_with_error(self):
        await self._reset()
        activity = await agentActivityService.add_activity(
            gt_agent=_fake_agent(), activity_type=AgentActivityType.TOOL_CALL,
        )
        updated = await agentActivityService.update_activity_progress(activity.id, status=AgentActivityStatus.FAILED, error_message="tool execution failed")
        assert updated.status == AgentActivityStatus.FAILED
        assert updated.error_message == "tool execution failed"
        assert updated.finished_at is not None

    async def test_fail_started_activities_marks_only_started_rows_failed(self):
        await self._reset()
        started_1 = await agentActivityService.add_activity(
            gt_agent=_fake_agent(agent_id=7), activity_type=AgentActivityType.LLM_INFER,
        )
        started_2 = await agentActivityService.add_activity(
            gt_agent=_fake_agent(agent_id=7), activity_type=AgentActivityType.TOOL_CALL,
        )
        finished = await agentActivityService.add_activity(
            gt_agent=_fake_agent(agent_id=7),
            activity_type=AgentActivityType.AGENT_STATE,
            status=AgentActivityStatus.SUCCEEDED,
        )
        other_agent = await agentActivityService.add_activity(
            gt_agent=_fake_agent(agent_id=8), activity_type=AgentActivityType.LLM_INFER,
        )

        updated = await agentActivityService.fail_started_activities(7, error_message="cancelled by user")

        assert {item.id for item in updated} == {started_1.id, started_2.id}
        assert all(item.status == AgentActivityStatus.FAILED for item in updated)
        assert all(item.error_message == "cancelled by user" for item in updated)
        assert all(item.finished_at is not None for item in updated)

        started_1_row = await GtAgentActivity.aio_get(GtAgentActivity.id == started_1.id)
        started_2_row = await GtAgentActivity.aio_get(GtAgentActivity.id == started_2.id)
        finished_row = await GtAgentActivity.aio_get(GtAgentActivity.id == finished.id)
        other_agent_row = await GtAgentActivity.aio_get(GtAgentActivity.id == other_agent.id)

        assert started_1_row.status == AgentActivityStatus.FAILED
        assert started_2_row.status == AgentActivityStatus.FAILED
        assert finished_row.status == AgentActivityStatus.SUCCEEDED
        assert other_agent_row.status == AgentActivityStatus.STARTED
