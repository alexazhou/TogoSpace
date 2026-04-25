"""测试 is_default 团队排序逻辑。

验证导入时 is_default=True 的团队优先导入（获得更小的 id，排在列表第一位）。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service import presetService
from util.configTypes import TeamConfig


@pytest.mark.asyncio
async def test_import_teams_from_app_config_sorts_by_is_default(monkeypatch):
    """验证 _import_teams_from_app_config 按 is_default 排序后导入。"""
    import_order: list[str] = []

    # 模拟 AppConfig 返回未排序的团队列表（非默认在前，默认在后）
    mock_teams = [
        TeamConfig(name="团队B", uuid="team-b", is_default=False, auto_start=True, agents=[]),
        TeamConfig(name="团队A", uuid="team-a", is_default=True, auto_start=True, agents=[]),
        TeamConfig(name="团队C", uuid="team-c", is_default=False, auto_start=True, agents=[]),
    ]

    mock_app_config = SimpleNamespace(teams=mock_teams, role_templates=[])

    monkeypatch.setattr(presetService.configUtil, "get_app_config", lambda: mock_app_config)

    async def _mock_import_team(team_config: TeamConfig):
        import_order.append(team_config.name)
        return SimpleNamespace(id=len(import_order), name=team_config.name)

    monkeypatch.setattr(presetService, "_import_team_from_config", _mock_import_team)
    monkeypatch.setattr(presetService, "_import_role_templates_from_app_config", AsyncMock())

    await presetService._import_teams_from_app_config()

    # is_default=True 的团队应该先导入（获得更小的 id）
    assert import_order == ["团队A", "团队B", "团队C"]