"""Team 改名时工作目录同步迁移的单元测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from exception import TogoException
from service import teamService
from util import workspaceUtil
from util.configUtil.configTypes import AppConfig, SettingConfig


def _app_config(workspace_root) -> AppConfig:
    return AppConfig(
        setting=SettingConfig(
            llm_services=[],
            default_llm_server=None,
            workspace_root=str(workspace_root),
        ),
    )


def _mock_managers(monkeypatch, team, save_team, workspace_root):
    monkeypatch.setattr(teamService.gtTeamManager, "get_team_by_id", AsyncMock(return_value=team))
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", AsyncMock(return_value=None))
    monkeypatch.setattr(teamService.gtTeamManager, "save_team", save_team)
    monkeypatch.setattr(workspaceUtil.configUtil, "get_app_config", lambda: _app_config(workspace_root))


@pytest.mark.asyncio
async def test_rename_team_moves_workspace_dir(monkeypatch, tmp_path):
    """Team 改名后，workspace/<old> 应整体移动到 workspace/<new>。"""
    team = SimpleNamespace(id=1, name="old", config={}, enabled=True)
    save_team = AsyncMock(side_effect=lambda saved: saved)
    _mock_managers(monkeypatch, team, save_team, tmp_path)

    old_dir = tmp_path / "old"
    (old_dir / "shared").mkdir(parents=True)
    (old_dir / "shared" / "todo.txt").write_text("todo", encoding="utf-8")
    (old_dir / "agent" / "alice").mkdir(parents=True)
    (old_dir / "agent" / "alice" / "note.txt").write_text("note", encoding="utf-8")

    saved = await teamService.rename_team(1, "new")

    new_dir = tmp_path / "new"
    assert saved.name == "new"
    assert not old_dir.exists()
    assert (new_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "todo"
    assert (new_dir / "agent" / "alice" / "note.txt").read_text(encoding="utf-8") == "note"
    save_team.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_team_keeps_custom_working_directory(monkeypatch, tmp_path):
    """自定义 working_directory 的团队不参与托管目录迁移。"""
    team = SimpleNamespace(
        id=1,
        name="old",
        config={"working_directory": str(tmp_path / "custom")},
        enabled=True,
    )
    save_team = AsyncMock(side_effect=lambda saved: saved)
    _mock_managers(monkeypatch, team, save_team, tmp_path)

    managed_dir = tmp_path / "old"
    managed_dir.mkdir()

    saved = await teamService.rename_team(1, "new")

    assert saved.name == "new"
    assert managed_dir.exists()
    assert not (tmp_path / "new").exists()


@pytest.mark.asyncio
async def test_rename_team_rejects_invalid_name(monkeypatch, tmp_path):
    """新名称含路径分隔符等特殊字符时应直接拒绝，不改目录也不写库。"""
    team = SimpleNamespace(id=1, name="old", config={}, enabled=True)
    save_team = AsyncMock(side_effect=lambda saved: saved)
    _mock_managers(monkeypatch, team, save_team, tmp_path)

    old_dir = tmp_path / "old"
    old_dir.mkdir()

    with pytest.raises(TogoException, match="不能包含非法字符"):
        await teamService.rename_team(1, "../evil")

    save_team.assert_not_awaited()
    assert old_dir.exists()
    assert not (tmp_path / "evil").exists()


@pytest.mark.asyncio
async def test_rename_team_aborts_on_workdir_conflict(monkeypatch, tmp_path):
    """目标目录已存在时应中止，且不写库。"""
    team = SimpleNamespace(id=1, name="old", config={}, enabled=True)
    save_team = AsyncMock(side_effect=lambda saved: saved)
    _mock_managers(monkeypatch, team, save_team, tmp_path)

    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()

    with pytest.raises(TogoException, match="目标目录已存在"):
        await teamService.rename_team(1, "new")

    save_team.assert_not_awaited()
    assert (tmp_path / "old").exists()
    assert (tmp_path / "new").exists()


@pytest.mark.asyncio
async def test_rename_team_rolls_back_dir_on_save_failure(monkeypatch, tmp_path):
    """目录已迁移但写库失败时，应把目录改回原名。"""
    team = SimpleNamespace(id=1, name="old", config={}, enabled=True)
    save_team = AsyncMock(side_effect=RuntimeError("db down"))
    _mock_managers(monkeypatch, team, save_team, tmp_path)

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "todo.txt").write_text("todo", encoding="utf-8")

    with pytest.raises(RuntimeError, match="db down"):
        await teamService.rename_team(1, "new")

    assert (old_dir / "todo.txt").read_text(encoding="utf-8") == "todo"
    assert not (tmp_path / "new").exists()
