from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.agentService import core
from util.configTypes import TeamConfig, AgentConfig, TeamRoomConfig


class _DummyAgent:
    def __init__(self, team_id: int) -> None:
        self.closed = False
        self.gt_agent = SimpleNamespace(team_id=team_id)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_reload_team_rebuilds_only_target_team(monkeypatch):
    target = _DummyAgent(team_id=1)
    other = _DummyAgent(team_id=2)
    monkeypatch.setattr(core, "_agents", {11: target, 22: other})

    mock_load_team = AsyncMock()
    monkeypatch.setattr(core, "_load_team", mock_load_team)

    await core.reload_team(1, workspace_root="/tmp/ws")

    assert target.closed is True
    assert other.closed is False
    assert 11 not in core._agents
    assert 22 in core._agents
    mock_load_team.assert_awaited_once_with(1, workspace_root="/tmp/ws")


@pytest.mark.asyncio
async def test_reload_team_no_target_only_closes_existing(monkeypatch):
    old = _DummyAgent(team_id=1)
    monkeypatch.setattr(core, "_agents", {11: old})

    mock_load_team = AsyncMock()
    monkeypatch.setattr(core, "_load_team", mock_load_team)

    await core.reload_team(1)

    assert old.closed is True
    assert core._agents == {}
    mock_load_team.assert_awaited_once_with(1, workspace_root=None)
