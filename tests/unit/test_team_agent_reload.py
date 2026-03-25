from unittest.mock import AsyncMock

import pytest

from service.agentService import core


class _DummyAgent:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_reload_team_agents_rebuilds_only_target_team(monkeypatch):
    target = _DummyAgent()
    other = _DummyAgent()
    monkeypatch.setattr(core, "_agents", {"实习生@default": target, "researcher@other": other})

    mock_load_team_ids = AsyncMock()
    mock_create_team_agents = AsyncMock()
    monkeypatch.setattr(core, "load_team_ids", mock_load_team_ids)
    monkeypatch.setattr(core, "create_team_agents", mock_create_team_agents)

    teams_config = [
        {
            "name": "default",
            "members": [{"name": "实习生", "agent": "intern_tsp"}],
            "preset_rooms": [{"name": "实习生", "members": ["Operator", "实习生"]}],
        },
        {
            "name": "other",
            "members": [{"name": "researcher", "agent": "researcher"}],
            "preset_rooms": [],
        },
    ]

    await core.reload_team_agents("default", teams_config, workspace_root="/tmp/ws")

    assert target.closed is True
    assert other.closed is False
    assert "实习生@default" not in core._agents
    assert "researcher@other" in core._agents
    mock_load_team_ids.assert_awaited_once_with(teams_config)
    mock_create_team_agents.assert_awaited_once_with(
        [core.normalize_team_config(teams_config[0])],
        workspace_root="/tmp/ws",
    )


@pytest.mark.asyncio
async def test_reload_team_agents_no_target_only_closes_existing(monkeypatch):
    old = _DummyAgent()
    monkeypatch.setattr(core, "_agents", {"实习生@default": old})

    mock_load_team_ids = AsyncMock()
    mock_create_team_agents = AsyncMock()
    monkeypatch.setattr(core, "load_team_ids", mock_load_team_ids)
    monkeypatch.setattr(core, "create_team_agents", mock_create_team_agents)

    teams_config = [{"name": "another", "members": [], "preset_rooms": []}]

    await core.reload_team_agents("default", teams_config)

    assert old.closed is True
    assert core._agents == {}
    mock_load_team_ids.assert_awaited_once_with(teams_config)
    mock_create_team_agents.assert_not_awaited()
