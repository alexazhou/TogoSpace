from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from service import agentService, roomService, schedulerService, teamService
@pytest.mark.asyncio
async def test_hot_reload_team_refreshes_agents_before_rooms(monkeypatch):
    call_order: list[str] = []

    async def _get_team(_name: str):
        return SimpleNamespace(id=1, name="default")

    def _stop_team(_name: str):
        call_order.append("stop_team")

    async def _reload_team_agents(_name: str):
        call_order.append("reload_team_agents")

    async def _refresh_scheduler(_name: str):
        call_order.append("refresh_scheduler")

    async def _refresh_rooms(_team_id: int):
        call_order.append("refresh_rooms")

    async def _exit_init_rooms(_team_name: str):
        call_order.append("exit_init_rooms")
        return 0

    monkeypatch.setattr(teamService.gtTeamManager, "get_team", _get_team)

    monkeypatch.setattr(schedulerService, "stop_team", _stop_team)
    monkeypatch.setattr(agentService, "reload_team_agents_from_db", _reload_team_agents)
    monkeypatch.setattr(schedulerService, "refresh_team_config", _refresh_scheduler)
    monkeypatch.setattr(roomService, "refresh_rooms_for_team", _refresh_rooms)
    monkeypatch.setattr(roomService, "exit_init_rooms", _exit_init_rooms)

    await teamService.hot_reload_team("default")

    assert call_order == [
        "stop_team",
        "reload_team_agents",
        "refresh_scheduler",
        "refresh_rooms",
        "exit_init_rooms",
    ]


@pytest.mark.asyncio
async def test_hot_reload_team_returns_if_target_not_found(monkeypatch):
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", AsyncMock(return_value=None))

    stop_team = Mock()
    reload_team_agents = AsyncMock()
    refresh_scheduler = AsyncMock()
    refresh_rooms = AsyncMock()

    monkeypatch.setattr(schedulerService, "stop_team", stop_team)
    monkeypatch.setattr(agentService, "reload_team_agents_from_db", reload_team_agents)
    monkeypatch.setattr(schedulerService, "refresh_team_config", refresh_scheduler)
    monkeypatch.setattr(roomService, "refresh_rooms_for_team", refresh_rooms)

    await teamService.hot_reload_team("missing")

    stop_team.assert_called_once_with("missing")
    reload_team_agents.assert_awaited_once_with("missing")
    refresh_scheduler.assert_awaited_once_with("missing")
    refresh_rooms.assert_not_awaited()
