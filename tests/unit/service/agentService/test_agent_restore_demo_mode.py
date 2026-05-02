from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants import AgentStatus, AgentTaskStatus
from service.agentService import core as agent_core


@pytest.mark.asyncio
async def test_restore_agent_runtime_state_skips_fail_running_tasks_in_demo_readonly(monkeypatch):
    agent = SimpleNamespace(
        gt_agent=SimpleNamespace(id=7),
        task_consumer=SimpleNamespace(status=None),
        inject_history_messages=MagicMock(),
    )
    histories = [SimpleNamespace(id=1)]
    fail_running_tasks = AsyncMock()

    monkeypatch.setattr(agent_core.persistenceService, "load_agent_history_message", AsyncMock(return_value=histories))
    monkeypatch.setattr(agent_core.persistenceService, "fail_running_tasks", fail_running_tasks)
    monkeypatch.setattr(agent_core.gtAgentTaskManager, "get_first_unfinish_task", AsyncMock(return_value=None))
    monkeypatch.setattr(
        agent_core.configUtil,
        "get_app_config",
        lambda: SimpleNamespace(setting=SimpleNamespace(demo_mode=SimpleNamespace(read_only=True))),
    )

    await agent_core._restore_agent_runtime_state(
        agent,
        running_task_error_message="restart-reason",
    )

    agent.inject_history_messages.assert_called_once_with(histories)
    fail_running_tasks.assert_not_awaited()
    assert agent.task_consumer.status == AgentStatus.IDLE


@pytest.mark.asyncio
async def test_restore_agent_runtime_state_still_marks_failed_when_not_demo(monkeypatch):
    agent = SimpleNamespace(
        gt_agent=SimpleNamespace(id=8),
        task_consumer=SimpleNamespace(status=None),
        inject_history_messages=MagicMock(),
    )
    failed_task = SimpleNamespace(status=AgentTaskStatus.FAILED)
    fail_running_tasks = AsyncMock()

    monkeypatch.setattr(agent_core.persistenceService, "load_agent_history_message", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_core.persistenceService, "fail_running_tasks", fail_running_tasks)
    monkeypatch.setattr(agent_core.gtAgentTaskManager, "get_first_unfinish_task", AsyncMock(return_value=failed_task))
    monkeypatch.setattr(
        agent_core.configUtil,
        "get_app_config",
        lambda: SimpleNamespace(setting=SimpleNamespace(demo_mode=SimpleNamespace(read_only=False))),
    )

    await agent_core._restore_agent_runtime_state(
        agent,
        running_task_error_message="restart-reason",
    )

    fail_running_tasks.assert_awaited_once_with(8, error_message="restart-reason")
    assert agent.task_consumer.status == AgentStatus.FAILED
