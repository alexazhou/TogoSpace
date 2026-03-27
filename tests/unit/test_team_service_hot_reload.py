from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service import agentService, roomService, schedulerService, teamService
from util.configTypes import TeamConfig


@pytest.mark.asyncio
async def test_hot_reload_team_refreshes_agents_before_rooms(monkeypatch):
    call_order: list[str] = []
    team_configs = [TeamConfig(name="default", members=[], preset_rooms=[])]

    async def _reload_from_db():
        return team_configs

    async def _get_team(_name: str):
        return SimpleNamespace(id=1, name="default")

    def _stop_team(_name: str):
        call_order.append("stop_team")

    async def _reload_team_agents(_name: str, _cfgs):
        call_order.append("reload_team_agents")

    def _refresh_scheduler(_name: str, _cfgs):
        call_order.append("refresh_scheduler")

    async def _refresh_rooms(_team_id: int, _cfgs):
        call_order.append("refresh_rooms")

    def _exit_init_rooms(_team_name: str):
        call_order.append("exit_init_rooms")
        return 0

    monkeypatch.setattr(teamService, "reload_from_db", _reload_from_db)
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", _get_team)

    monkeypatch.setattr(schedulerService, "stop_team", _stop_team)
    monkeypatch.setattr(agentService, "reload_team_agents", _reload_team_agents)
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
    monkeypatch.setattr(teamService, "reload_from_db", AsyncMock(return_value=[]))

    stop_team = AsyncMock()
    reload_team_agents = AsyncMock()
    refresh_scheduler = AsyncMock()
    refresh_rooms = AsyncMock()

    monkeypatch.setattr(schedulerService, "stop_team", stop_team)
    monkeypatch.setattr(agentService, "reload_team_agents", reload_team_agents)
    monkeypatch.setattr(schedulerService, "refresh_team_config", refresh_scheduler)
    monkeypatch.setattr(roomService, "refresh_rooms_for_team", refresh_rooms)

    await teamService.hot_reload_team("missing")

    stop_team.assert_not_called()
    reload_team_agents.assert_not_awaited()
    refresh_scheduler.assert_not_called()
    refresh_rooms.assert_not_awaited()
