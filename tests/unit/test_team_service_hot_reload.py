from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service import agentService, roomService, schedulerService, teamService


@pytest.mark.asyncio
async def test_stop_team_runtime_stops_scheduler_before_unloading_runtime(monkeypatch):
    call_order: list[str] = []

    def _stop_scheduler_team(_team_id: int):
        call_order.append("stop_scheduler_team")

    async def _unload_team(_team_id: int):
        call_order.append("unload_team")

    async def _close_team_rooms(_team_id: int):
        call_order.append("close_team_rooms")

    monkeypatch.setattr(schedulerService, "stop_scheduler_team", _stop_scheduler_team)
    monkeypatch.setattr(agentService, "unload_team", _unload_team)
    monkeypatch.setattr(roomService, "close_team_rooms", _close_team_rooms)

    await teamService.stop_team_runtime(1)

    assert call_order == [
        "stop_scheduler_team",
        "unload_team",
        "close_team_rooms",
    ]


@pytest.mark.asyncio
async def test_restore_team_restores_agent_state_before_rooms(monkeypatch):
    call_order: list[str] = []

    async def _get_team_by_id(_team_id: int):
        return SimpleNamespace(id=1, name="default", enabled=1)

    async def _load_team_agents(_team_id: int, workspace_root=None):
        assert workspace_root == "/tmp/ws"
        call_order.append("load_team")

    async def _refresh_rooms(_team_id: int):
        call_order.append("refresh_rooms")

    async def _restore_team_agents_runtime_state(_team_id: int, running_task_error_message: str):
        assert running_task_error_message == "restart-reason"
        call_order.append("restore_agent_state")

    async def _restore_team_rooms_runtime_state(_team_id: int):
        call_order.append("restore_room_state")

    async def _start_scheduling(_team_name: str):
        assert _team_name == "default"
        call_order.append("start_scheduling")

    monkeypatch.setattr(teamService.gtTeamManager, "get_team_by_id", _get_team_by_id)
    monkeypatch.setattr(agentService, "load_team_agents", _load_team_agents)
    monkeypatch.setattr(roomService, "load_team_rooms", _refresh_rooms)
    monkeypatch.setattr(agentService, "restore_team_agents_runtime_state", _restore_team_agents_runtime_state)
    monkeypatch.setattr(roomService, "restore_team_rooms_runtime_state", _restore_team_rooms_runtime_state)
    monkeypatch.setattr(schedulerService, "start_scheduling", _start_scheduling)

    await teamService.restore_team(
        1,
        workspace_root="/tmp/ws",
        running_task_error_message="restart-reason",
    )

    assert call_order == [
        "load_team",
        "refresh_rooms",
        "restore_agent_state",
        "restore_room_state",
        "start_scheduling",
    ]


@pytest.mark.asyncio
async def test_restore_team_returns_if_target_not_found(monkeypatch):
    monkeypatch.setattr(teamService.gtTeamManager, "get_team_by_id", AsyncMock(return_value=None))

    load_team_agents = AsyncMock()
    refresh_rooms = AsyncMock()
    restore_team_agents_runtime_state = AsyncMock()
    restore_team_rooms_runtime_state = AsyncMock()
    start_scheduling = AsyncMock()

    monkeypatch.setattr(agentService, "load_team_agents", load_team_agents)
    monkeypatch.setattr(roomService, "load_team_rooms", refresh_rooms)
    monkeypatch.setattr(agentService, "restore_team_agents_runtime_state", restore_team_agents_runtime_state)
    monkeypatch.setattr(roomService, "restore_team_rooms_runtime_state", restore_team_rooms_runtime_state)
    monkeypatch.setattr(schedulerService, "start_scheduling", start_scheduling)

    await teamService.restore_team(1)

    load_team_agents.assert_not_awaited()
    refresh_rooms.assert_not_awaited()
    restore_team_agents_runtime_state.assert_not_awaited()
    restore_team_rooms_runtime_state.assert_not_awaited()
    start_scheduling.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_team_skips_disabled_team(monkeypatch):
    monkeypatch.setattr(
        teamService.gtTeamManager,
        "get_team_by_id",
        AsyncMock(return_value=SimpleNamespace(id=1, name="default", enabled=0)),
    )

    load_team_agents = AsyncMock()
    monkeypatch.setattr(agentService, "load_team_agents", load_team_agents)

    await teamService.restore_team(1)

    load_team_agents.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_team_runtime_runs_stop_then_restore(monkeypatch):
    call_order: list[str] = []

    async def _stop_team_runtime(_team_id: int):
        call_order.append("stop_team_runtime")

    async def _restore_team(_team_id: int, workspace_root=None, running_task_error_message=""):
        assert workspace_root == "/tmp/ws"
        assert running_task_error_message == "restart-reason"
        call_order.append("restore_team")

    monkeypatch.setattr(teamService, "stop_team_runtime", _stop_team_runtime)
    monkeypatch.setattr(teamService, "restore_team", _restore_team)

    await teamService.restart_team_runtime(
        1,
        workspace_root="/tmp/ws",
        running_task_error_message="restart-reason",
    )

    assert call_order == ["stop_team_runtime", "restore_team"]


@pytest.mark.asyncio
async def test_hot_reload_team_restarts_runtime(monkeypatch):
    async def _get_team(_name: str):
        return SimpleNamespace(id=1, name="default")

    restart_team_runtime = AsyncMock()
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", _get_team)
    monkeypatch.setattr(teamService, "restart_team_runtime", restart_team_runtime)

    await teamService.hot_reload_team("default")

    restart_team_runtime.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_hot_reload_team_returns_if_target_not_found(monkeypatch):
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", AsyncMock(return_value=None))

    restart_team_runtime = AsyncMock()
    monkeypatch.setattr(teamService, "restart_team_runtime", restart_team_runtime)

    await teamService.hot_reload_team("missing")

    restart_team_runtime.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_team_enabled_disables_runtime(monkeypatch):
    monkeypatch.setattr(
        teamService.gtTeamManager,
        "get_team_by_id",
        AsyncMock(return_value=SimpleNamespace(id=1, name="default")),
    )
    monkeypatch.setattr(teamService.gtTeamManager, "set_team_enabled", AsyncMock())
    stop_team_runtime = AsyncMock()
    monkeypatch.setattr(teamService, "stop_team_runtime", stop_team_runtime)

    await teamService.set_team_enabled(1, False)

    stop_team_runtime.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_set_team_enabled_restores_runtime(monkeypatch):
    monkeypatch.setattr(
        teamService.gtTeamManager,
        "get_team_by_id",
        AsyncMock(return_value=SimpleNamespace(id=1, name="default")),
    )
    monkeypatch.setattr(teamService.gtTeamManager, "set_team_enabled", AsyncMock())
    restore_team = AsyncMock()
    monkeypatch.setattr(teamService, "restore_team", restore_team)

    await teamService.set_team_enabled(1, True)

    restore_team.assert_awaited_once_with(1)
