"""save_agent 工具在成员改名 / 写库失败时同步迁移私有目录的单元测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import DriverType, EmployStatus
from model.dbModel.gtAgent import GtAgent
from service.agentService import core
from service.funcToolService import tools
from service.roomService import ToolCallContext
from util import workspaceUtil
from util.configUtil.configTypes import AppConfig, SettingConfig

TEAM_ID = 1


def _app_config(workspace_root) -> AppConfig:
    return AppConfig(
        setting=SettingConfig(
            llm_services=[],
            default_llm_server=None,
            workspace_root=str(workspace_root),
        ),
    )


def _make_existing(name: str = "alice") -> GtAgent:
    return GtAgent(
        id=11,
        team_id=TEAM_ID,
        name=name,
        role_template_id=21,
        employ_status=EmployStatus.ON_BOARD,
        model="",
        driver=DriverType.NATIVE,
        i18n={},
    )


def _mock_dependencies(monkeypatch, tmp_path, agent: GtAgent, batch_save_agents) -> None:
    team = SimpleNamespace(id=TEAM_ID, name="demo", config={})
    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", AsyncMock(return_value=team))
    monkeypatch.setattr(workspaceUtil.configUtil, "get_app_config", lambda: _app_config(tmp_path))
    monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=agent))
    monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", batch_save_agents)
    monkeypatch.setattr(core, "switch_agent_to_host", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tools.containerService, "delete_agent_containers_safely", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        tools.gtRoleTemplateManager,
        "get_role_template_by_name",
        AsyncMock(return_value=SimpleNamespace(id=21, name="writer")),
    )


def _context() -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=TEAM_ID, chat_room=None)


@pytest.mark.asyncio
async def test_save_agent_renames_private_dir(monkeypatch, tmp_path) -> None:
    """按 agent_id 改名后，<team>/agent/<old> 应移动到 <team>/agent/<new>。"""
    agent = _make_existing("alice")
    _mock_dependencies(monkeypatch, tmp_path, agent, AsyncMock())

    old_dir = tmp_path / "demo" / "agent" / "alice"
    old_dir.mkdir(parents=True)
    (old_dir / "notes.txt").write_text("hello", encoding="utf-8")

    result = await tools.save_agent(
        name="bob",
        role_template_name="writer",
        agent_id=11,
        _context=_context(),
    )

    new_dir = tmp_path / "demo" / "agent" / "bob"
    assert result["success"]
    assert agent.name == "bob"
    assert not old_dir.exists()
    assert (new_dir / "notes.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_save_agent_cleans_containers_before_renaming_dir(monkeypatch, tmp_path) -> None:
    """改名必须先清旧容器再移动目录：bind mount 固化的是旧绝对路径（§7）。"""
    agent = _make_existing("alice")
    _mock_dependencies(monkeypatch, tmp_path, agent, AsyncMock())

    old_dir = tmp_path / "demo" / "agent" / "alice"
    old_dir.mkdir(parents=True)

    order: list[str] = []

    async def _switch(agent_id: int) -> bool:
        order.append(f"switch:{agent_id}")
        return True

    async def _clean(agent_id: int) -> int:
        order.append(f"clean:{agent_id}")
        return 1

    real_rename = core.workspaceUtil.rename_agent_workdirs

    def _record_rename(*args, **kwargs):
        order.append("rename_dir")
        return real_rename(*args, **kwargs)

    monkeypatch.setattr(core, "switch_agent_to_host", _switch)
    monkeypatch.setattr(tools.containerService, "delete_agent_containers_safely", _clean)
    monkeypatch.setattr(core.workspaceUtil, "rename_agent_workdirs", _record_rename)

    result = await tools.save_agent(
        name="bob",
        role_template_name="writer",
        agent_id=11,
        _context=_context(),
    )

    assert result["success"]
    # 顺序不可反：先切回宿主，再删容器，最后才移动目录（§7）
    assert order == ["switch:11", "clean:11", "rename_dir"]


@pytest.mark.asyncio
async def test_save_agent_rollback_does_not_clean_containers_again(monkeypatch, tmp_path) -> None:
    """写库失败回滚目录时不再删一次容器：容器在移动目录前已清理。"""
    agent = _make_existing("alice")
    _mock_dependencies(
        monkeypatch, tmp_path, agent, AsyncMock(side_effect=RuntimeError("db down"))
    )
    clean = AsyncMock(return_value=0)
    monkeypatch.setattr(tools.containerService, "delete_agent_containers_safely", clean)

    (tmp_path / "demo" / "agent" / "alice").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="db down"):
        await tools.save_agent(
            name="bob",
            role_template_name="writer",
            agent_id=11,
            _context=_context(),
        )

    clean.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_save_agent_rejects_invalid_name(monkeypatch, tmp_path) -> None:
    """名称含路径分隔符等特殊字符时返回失败，不写库也不碰目录。"""
    agent = _make_existing("alice")
    batch_save_agents = AsyncMock()
    _mock_dependencies(monkeypatch, tmp_path, agent, batch_save_agents)

    result = await tools.save_agent(
        name="../bob",
        role_template_name="writer",
        _context=_context(),
    )

    assert not result["success"]
    assert "不能包含" in result["message"]
    batch_save_agents.assert_not_awaited()
    assert agent.name == "alice"


@pytest.mark.asyncio
async def test_save_agent_rolls_back_dir_when_save_fails(monkeypatch, tmp_path) -> None:
    """目录已改名但写库失败时，应把目录改回原名并抛出异常。"""
    agent = _make_existing("alice")
    _mock_dependencies(monkeypatch, tmp_path, agent, AsyncMock(side_effect=RuntimeError("db down")))

    old_dir = tmp_path / "demo" / "agent" / "alice"
    old_dir.mkdir(parents=True)
    (old_dir / "notes.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(RuntimeError, match="db down"):
        await tools.save_agent(
            name="bob",
            role_template_name="writer",
            agent_id=11,
            _context=_context(),
        )

    assert (old_dir / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "demo" / "agent" / "bob").exists()
