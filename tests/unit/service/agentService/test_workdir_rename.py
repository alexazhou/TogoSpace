"""Agent 私有目录随 name 变更同步重命名的单元测试。"""

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import DriverType, EmployStatus
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from service.agentService import core
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


def _make_agent(agent_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=agent_id,
        team_id=1,
        name=name,
        role_template_id=21,
        model="",
        driver=DriverType.NATIVE,
        employ_status=EmployStatus.ON_BOARD,
        i18n={},
        allow_tools=None,
    )


@asynccontextmanager
async def _noop_atomic():
    yield


def _mock_managers(monkeypatch, team, agents, save_agents):
    async def _get_team(_team_id: int):
        return team

    async def _get_agents(_team_id: int, status=None):
        return agents

    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", _get_team)
    monkeypatch.setattr(core.gtAgentManager, "get_team_all_agents", _get_agents)
    monkeypatch.setattr(core.gtAgentManager, "batch_save_agents", save_agents)
    monkeypatch.setattr(core.gtAgentManager, "batch_update_agent_status", AsyncMock())
    monkeypatch.setattr(core, "atomic", _noop_atomic)


@pytest.mark.asyncio
async def test_overwrite_team_agents_renames_agent_private_dir(monkeypatch, tmp_path):
    """成员改名后，<team>/agent/<old> 应原样移动到 <team>/agent/<new>。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    save_agents = AsyncMock()

    _mock_managers(monkeypatch, team, [alice], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    old_dir = tmp_path / "demo" / "agent" / "alice"
    old_dir.mkdir(parents=True)
    (old_dir / "notes.txt").write_text("hello", encoding="utf-8")

    payload = [GtAgent(id=11, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE)]
    updated = await core.overwrite_team_agents(1, payload)

    new_dir = tmp_path / "demo" / "agent" / "bob"
    assert updated == [alice]
    assert alice.name == "bob"
    assert not old_dir.exists()
    assert (new_dir / "notes.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_overwrite_team_agents_aborts_on_swapping_names(monkeypatch, tmp_path):
    """互换名字时目标目录已被占用，单次保存直接报冲突，不写库。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    bob = _make_agent(12, "bob")
    save_agents = AsyncMock()

    _mock_managers(monkeypatch, team, [alice, bob], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    alice_dir = tmp_path / "demo" / "agent" / "alice"
    bob_dir = tmp_path / "demo" / "agent" / "bob"
    alice_dir.mkdir(parents=True)
    bob_dir.mkdir(parents=True)
    (alice_dir / "who.txt").write_text("alice", encoding="utf-8")
    (bob_dir / "who.txt").write_text("bob", encoding="utf-8")

    payload = [
        GtAgent(id=11, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE),
        GtAgent(id=12, team_id=1, name="alice", role_template_id=21, model="", driver=DriverType.NATIVE),
    ]
    with pytest.raises(TogoException, match="目标目录已存在"):
        await core.overwrite_team_agents(1, payload)

    save_agents.assert_not_awaited()
    assert (alice_dir / "who.txt").read_text(encoding="utf-8") == "alice"
    assert (bob_dir / "who.txt").read_text(encoding="utf-8") == "bob"


@pytest.mark.asyncio
async def test_overwrite_team_agents_aborts_on_workdir_conflict(monkeypatch, tmp_path):
    """目标目录已存在（不在本批次改名中）时应中止，且不写库、不移动文件。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    save_agents = AsyncMock()

    _mock_managers(monkeypatch, team, [alice], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    alice_dir = tmp_path / "demo" / "agent" / "alice"
    bob_dir = tmp_path / "demo" / "agent" / "bob"
    alice_dir.mkdir(parents=True)
    bob_dir.mkdir(parents=True)
    (alice_dir / "who.txt").write_text("alice", encoding="utf-8")
    (bob_dir / "who.txt").write_text("bob", encoding="utf-8")

    payload = [GtAgent(id=11, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE)]
    with pytest.raises(TogoException, match="目标目录已存在"):
        await core.overwrite_team_agents(1, payload)

    save_agents.assert_not_awaited()
    assert (alice_dir / "who.txt").read_text(encoding="utf-8") == "alice"
    assert (bob_dir / "who.txt").read_text(encoding="utf-8") == "bob"


@pytest.mark.asyncio
async def test_overwrite_team_agents_rolls_back_dir_on_save_failure(monkeypatch, tmp_path):
    """目录已改名但写库失败时，应把目录改回原名。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    save_agents = AsyncMock(side_effect=RuntimeError("db down"))

    _mock_managers(monkeypatch, team, [alice], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    old_dir = tmp_path / "demo" / "agent" / "alice"
    old_dir.mkdir(parents=True)
    (old_dir / "notes.txt").write_text("hello", encoding="utf-8")

    payload = [GtAgent(id=11, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE)]
    with pytest.raises(RuntimeError, match="db down"):
        await core.overwrite_team_agents(1, payload)

    assert (old_dir / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "demo" / "agent" / "bob").exists()


@pytest.mark.asyncio
async def test_overwrite_team_agents_removes_offboarded_private_dir(monkeypatch, tmp_path):
    """成员离职后，其个人目录应被删除。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    bob = _make_agent(12, "bob")
    save_agents = AsyncMock()

    _mock_managers(monkeypatch, team, [alice, bob], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    alice_dir = tmp_path / "demo" / "agent" / "alice"
    bob_dir = tmp_path / "demo" / "agent" / "bob"
    alice_dir.mkdir(parents=True)
    bob_dir.mkdir(parents=True)
    (bob_dir / "notes.txt").write_text("bob", encoding="utf-8")

    payload = [GtAgent(id=11, team_id=1, name="alice", role_template_id=21, model="", driver=DriverType.NATIVE)]
    await core.overwrite_team_agents(1, payload)

    assert alice_dir.exists()
    assert not bob_dir.exists()


@pytest.mark.asyncio
async def test_overwrite_team_agents_keeps_dir_when_name_reused(monkeypatch, tmp_path):
    """离职者与在岗成员同名（名字复用）时，个人目录仍归在岗成员使用，不能删除。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    bob_on_board = _make_agent(11, "bob")
    bob_off_board = _make_agent(12, "bob")
    save_agents = AsyncMock()

    _mock_managers(monkeypatch, team, [bob_on_board, bob_off_board], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    bob_dir = tmp_path / "demo" / "agent" / "bob"
    bob_dir.mkdir(parents=True)
    (bob_dir / "notes.txt").write_text("keep", encoding="utf-8")

    payload = [GtAgent(id=11, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE)]
    await core.overwrite_team_agents(1, payload)

    assert (bob_dir / "notes.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_overwrite_team_agents_keeps_offboarded_dir_when_save_fails(monkeypatch, tmp_path):
    """写库失败时离职不成立，个人目录应保留。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    bob = _make_agent(12, "bob")
    save_agents = AsyncMock(side_effect=RuntimeError("db down"))

    _mock_managers(monkeypatch, team, [alice, bob], save_agents)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    bob_dir = tmp_path / "demo" / "agent" / "bob"
    bob_dir.mkdir(parents=True)
    (bob_dir / "notes.txt").write_text("bob", encoding="utf-8")

    payload = [GtAgent(id=11, team_id=1, name="alice", role_template_id=21, model="", driver=DriverType.NATIVE)]
    with pytest.raises(RuntimeError, match="db down"):
        await core.overwrite_team_agents(1, payload)

    assert (bob_dir / "notes.txt").read_text(encoding="utf-8") == "bob"


def test_resolve_shared_workdir_always_under_team_dir():
    """团队共享目录始终是 <团队目录>/shared，与 working_directory 无关。"""
    resolved = workspaceUtil.resolve_shared_workdir("/tmp/custom-team-dir")

    assert resolved == os.path.join("/tmp/custom-team-dir", "shared")


def test_resolve_agent_workdir_always_under_team_agent_dir():
    """Agent 私有目录始终是 <团队目录>/agent/<name>，与 working_directory 无关。"""
    resolved = workspaceUtil.resolve_agent_workdir("/tmp/custom-team-dir", "alice")

    assert resolved == os.path.join("/tmp/custom-team-dir", "agent", "alice")


def test_remove_agent_workdirs_uses_custom_working_directory(tmp_path):
    """自定义 working_directory 只改变团队目录，Agent 私有目录仍在 <team 工作目录>/agent 下。"""
    custom_dir = tmp_path / "custom"
    team = SimpleNamespace(id=1, name="demo", config={"working_directory": str(custom_dir)})
    bob_dir = custom_dir / "agent" / "bob"
    bob_dir.mkdir(parents=True)
    (bob_dir / "notes.txt").write_text("bob", encoding="utf-8")

    removed = workspaceUtil.remove_agent_workdirs(team.name, team.config, ["bob"], [], workspace_root=tmp_path)

    assert removed == [str(bob_dir)]
    assert not bob_dir.exists()


def test_rename_agent_workdirs_uses_custom_working_directory(tmp_path):
    """自定义 working_directory 的团队，Agent 改名同样迁移 <custom>/agent/<name>。"""
    custom_dir = tmp_path / "custom"
    team = SimpleNamespace(id=1, name="demo", config={"working_directory": str(custom_dir)})
    old_dir = custom_dir / "agent" / "alice"
    old_dir.mkdir(parents=True)
    (old_dir / "who.txt").write_text("alice", encoding="utf-8")

    moved = workspaceUtil.rename_agent_workdirs(
        team.name,
        team.config,
        [("alice", "bob")],
        workspace_root=tmp_path,
    )

    assert moved == [(str(old_dir), str(custom_dir / "agent" / "bob"))]
    assert (custom_dir / "agent" / "bob" / "who.txt").read_text(encoding="utf-8") == "alice"


def test_rename_agent_workdirs_skips_missing_source(tmp_path):
    """源目录不存在时（如成员从未启动过），改名应是空操作。"""
    team = SimpleNamespace(id=1, name="demo", config={})
    (tmp_path / "demo" / "agent" / "bob").mkdir(parents=True)

    moved = workspaceUtil.rename_agent_workdirs(
        team.name,
        team.config,
        [("alice", "bob")],
        workspace_root=tmp_path,
    )

    assert moved == []
    assert (tmp_path / "demo" / "agent" / "bob").exists()


def test_rename_agent_workdirs_renames_multiple_agents(tmp_path):
    """一次保存里多个 Agent 各自改名，目录内容应分别跟随。"""
    team = SimpleNamespace(id=1, name="demo", config={})
    agent_root = tmp_path / "demo" / "agent"
    (agent_root / "alice").mkdir(parents=True)
    (agent_root / "bob").mkdir()
    (agent_root / "alice" / "who.txt").write_text("alice", encoding="utf-8")
    (agent_root / "bob" / "who.txt").write_text("bob", encoding="utf-8")

    moved = workspaceUtil.rename_agent_workdirs(
        team.name,
        team.config,
        [("alice", "alice2"), ("bob", "bob2")],
        workspace_root=tmp_path,
    )

    assert len(moved) == 2
    assert not (agent_root / "alice").exists()
    assert not (agent_root / "bob").exists()
    assert (agent_root / "alice2" / "who.txt").read_text(encoding="utf-8") == "alice"
    assert (agent_root / "bob2" / "who.txt").read_text(encoding="utf-8") == "bob"


def test_rename_agent_workdirs_rolls_back_on_rename_failure(tmp_path, monkeypatch):
    """第二个目录改名失败时，已改名的目录应回到原名。"""
    team = SimpleNamespace(id=1, name="demo", config={})
    agent_root = tmp_path / "demo" / "agent"
    (agent_root / "alice").mkdir(parents=True)
    (agent_root / "bob").mkdir()
    (agent_root / "alice" / "who.txt").write_text("alice", encoding="utf-8")
    (agent_root / "bob" / "who.txt").write_text("bob", encoding="utf-8")

    real_rename = os.rename
    call_count = 0

    def _flaky_rename(src, dst):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("boom")
        return real_rename(src, dst)

    monkeypatch.setattr(workspaceUtil.os, "rename", _flaky_rename)

    with pytest.raises(OSError, match="boom"):
        workspaceUtil.rename_agent_workdirs(
            team.name,
            team.config,
            [("alice", "alice2"), ("bob", "bob2")],
            workspace_root=tmp_path,
        )

    assert (agent_root / "alice" / "who.txt").read_text(encoding="utf-8") == "alice"
    assert not (agent_root / "alice2").exists()
    assert (agent_root / "bob" / "who.txt").read_text(encoding="utf-8") == "bob"
    assert not (agent_root / "bob2").exists()
