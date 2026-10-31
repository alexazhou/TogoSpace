"""Team 创建时的名称校验测试。"""

from unittest.mock import AsyncMock

import pytest

from exception import TogoException
from service import teamService


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../evil", "bad/name", "CON", "CON.txt", "bad:name", ""])
async def test_create_team_rejects_invalid_name(monkeypatch, name: str) -> None:
    """名称会作为目录名：含路径分隔符、Windows 非法字符或保留名时直接拒绝，不触碰数据库。"""
    team_exists = AsyncMock()
    monkeypatch.setattr(teamService.gtTeamManager, "team_exists", team_exists)

    with pytest.raises(TogoException) as excinfo:
        await teamService.create_team(name)

    assert excinfo.value.error_code == "invalid_team_name"
    team_exists.assert_not_awaited()
