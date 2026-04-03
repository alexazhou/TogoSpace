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
async def test_reload_team_agents_rebuilds_only_target_team(monkeypatch):
    target = _DummyAgent(team_id=1)
    other = _DummyAgent(team_id=2)
    monkeypatch.setattr(core, "_agents", {11: target, 22: other})

    team_row = SimpleNamespace(id=1, name="default")
    agent_row = SimpleNamespace(name="实习生", role_template_id=1, id=11)
    template_row = SimpleNamespace(id=1)
    mock_get_team = AsyncMock(return_value=team_row)
    mock_get_agents = AsyncMock(return_value=[agent_row])
    mock_get_templates = AsyncMock(return_value=[template_row])
    mock_create_team_agents = AsyncMock()
    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", mock_get_team)
    monkeypatch.setattr(core.gtAgentManager, "get_team_agents", mock_get_agents)
    monkeypatch.setattr(core.gtRoleTemplateManager, "get_role_templates_by_ids", mock_get_templates)
    monkeypatch.setattr(core, "_create_team_agents", mock_create_team_agents)

    await core.reload_team_agents_from_db(1, workspace_root="/tmp/ws")

    assert target.closed is True
    assert other.closed is False
    assert 11 not in core._agents
    assert 22 in core._agents
    mock_create_team_agents.assert_awaited_once_with(
        team_row,
        [agent_row],
        {1: template_row},
        workspace_root="/tmp/ws",
    )


@pytest.mark.asyncio
async def test_reload_team_agents_no_target_only_closes_existing(monkeypatch):
    old = _DummyAgent(team_id=1)
    monkeypatch.setattr(core, "_agents", {11: old})

    mock_get_team = AsyncMock(return_value=None)
    mock_create_team_agents = AsyncMock()
    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", mock_get_team)
    monkeypatch.setattr(core, "_create_team_agents", mock_create_team_agents)

    await core.reload_team_agents_from_db(1)

    assert old.closed is True
    assert core._agents == {}
    mock_create_team_agents.assert_not_awaited()
