from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from service import agentService, roomService, schedulerService, teamService


@pytest.mark.asyncio
async def test_hot_reload_team_refreshes_agents_before_rooms(monkeypatch):
    call_order: list[str] = []

    async def _get_team(_name: str):
        return SimpleNamespace(id=1, name="default")

    def _stop_team(_team_id: int):
        call_order.append("stop_team")

    async def _reload_team(_team_id: int):
        call_order.append("reload_team")

    async def _refresh_rooms(_team_id: int):
        call_order.append("refresh_rooms")

    async def _restore_rooms(_team_id: int):
        call_order.append("restore_rooms")

    async def _start_scheduling(_team_name: str):
        call_order.append("start_scheduling")

    monkeypatch.setattr(teamService.gtTeamManager, "get_team", _get_team)

    monkeypatch.setattr(schedulerService, "stop_team", _stop_team)
    monkeypatch.setattr(agentService, "reload_team", _reload_team)
    monkeypatch.setattr(roomService, "refresh_rooms_for_team", _refresh_rooms)
    monkeypatch.setattr(roomService, "restore_state_for_team", _restore_rooms)
    monkeypatch.setattr(schedulerService, "start_scheduling", _start_scheduling)

    await teamService.hot_reload_team("default")

    assert call_order == [
        "stop_team",
        "reload_team",
        "refresh_rooms",
        "restore_rooms",
        "start_scheduling",
    ]


@pytest.mark.asyncio
async def test_hot_reload_team_returns_if_target_not_found(monkeypatch):
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", AsyncMock(return_value=None))

    stop_team = Mock()
    reload_team = AsyncMock()
    refresh_rooms = AsyncMock()
    restore_rooms = AsyncMock()

    monkeypatch.setattr(schedulerService, "stop_team", stop_team)
    monkeypatch.setattr(agentService, "reload_team", reload_team)
    monkeypatch.setattr(roomService, "refresh_rooms_for_team", refresh_rooms)
    monkeypatch.setattr(roomService, "restore_state_for_team", restore_rooms)

    await teamService.hot_reload_team("missing")

    stop_team.assert_not_called()
    reload_team.assert_not_awaited()
    refresh_rooms.assert_not_awaited()
    restore_rooms.assert_not_awaited()
