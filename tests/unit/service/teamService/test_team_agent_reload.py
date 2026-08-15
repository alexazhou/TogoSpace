from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from service.agentService import core
from util.configUtil.configTypes import AppConfig, SettingConfig
from constants import DriverType


class _DummyAgent:
    def __init__(self, team_id: int) -> None:
        self.closed = False
        self.gt_agent = SimpleNamespace(team_id=team_id)

    async def close(self) -> None:
        self.closed = True

    async def startup(self) -> None:
        pass


@pytest.mark.asyncio
async def test_unload_team_removes_only_target_team_runtime(monkeypatch: Any) -> None:
    target = _DummyAgent(team_id=1)
    other = _DummyAgent(team_id=2)
    monkeypatch.setattr(core, "_agents", {11: target, 22: other})

    await core.unload_team(1)

    assert target.closed is True
    assert other.closed is False
    assert 11 not in core._agents
    assert 22 in core._agents


@pytest.mark.asyncio
async def test_load_team_agents_delegates_to_internal_loader(monkeypatch: Any) -> None:
    mock_load_team_agents = AsyncMock()
    monkeypatch.setattr(core, "_load_team_agents", mock_load_team_agents)

    await core.load_team_agents(1, workspace_root="/tmp/ws")

    mock_load_team_agents.assert_awaited_once_with(1, workspace_root="/tmp/ws")


def test_resolve_team_workdir_prefers_explicit_working_directory() -> None:
    team = SimpleNamespace(name="default", config={"working_directory": "/tmp/custom-team-dir"})

    resolved = core._resolve_team_workdir(team, "/tmp/workspaces")

    assert resolved == "/tmp/custom-team-dir"


def test_resolve_team_workdir_falls_back_to_workspace_root() -> None:
    team = SimpleNamespace(name="default", config={})

    resolved = core._resolve_team_workdir(team, "/tmp/workspaces")

    assert resolved == "/tmp/workspaces/default"


def test_agent_model_resolution_logic() -> None:
    """测试 Agent model 的解析逻辑：优先使用 Agent 自身 model，其次 role template，最后配置。"""
    # 模拟各层级的 model 值
    agent_model = "agent-model"
    template_model = "template-model"
    default_model = "config-model"

    # Agent model 有值时，使用 Agent model
    result = agent_model or template_model or default_model
    assert result == "agent-model"

    # Agent model 为空，template model 有值时，使用 template model
    agent_model = ""
    result = agent_model or template_model or default_model
    assert result == "template-model"

    # Agent 和 template 都为空时，使用配置中的 default model
    agent_model = ""
    template_model = ""
    result = agent_model or template_model or default_model
    assert result == "config-model"


@pytest.mark.asyncio
async def test_load_team_agents_allows_startup_without_available_llm(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    team = SimpleNamespace(id=1, name="demo", config={})
    gt_agent = SimpleNamespace(id=11, team_id=1, name="alice", display_name="alice", role_template_id=21, model="", driver=DriverType.NATIVE, allow_tools=None, allow_skills=[])
    template = SimpleNamespace(id=21, name="alice", display_name="alice", model=None, soul="mock soul")
    started: list[int] = []

    class _FakeAgent:
        def __init__(
            self,
            *,
            gt_agent: Any,
            system_prompt: str,
            driver_config: Any = None,
            agent_workdir: str = "",
            is_root_leader: bool = False,
        ) -> None:
            self.gt_agent = gt_agent
            self.system_prompt = system_prompt
            self.driver_config = driver_config
            self.agent_workdir = agent_workdir
            self.is_root_leader = is_root_leader

        async def startup(self) -> None:
            started.append(self.gt_agent.id)

    async def _get_team_by_id(team_id: int) -> Any:
        assert team_id == 1
        return team

    async def _get_team_agents(team_id: int, status: Any = None) -> list[Any]:
        assert team_id == 1
        return [gt_agent]

    async def _get_role_templates_by_ids(role_template_ids: list[int]) -> list[Any]:
        assert role_template_ids == [21]
        return [template]

    async def _build_agent_system_prompt(**kwargs: Any) -> str:
        return "prompt"

    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", _get_team_by_id)
    monkeypatch.setattr(core.gtAgentManager, "get_team_all_agents", _get_team_agents)
    monkeypatch.setattr(core.gtRoleTemplateManager, "get_role_templates_by_ids", _get_role_templates_by_ids)
    monkeypatch.setattr(core.deptService, "get_dept_tree", AsyncMock(return_value=None))
    monkeypatch.setattr(core, "build_agent_system_prompt", _build_agent_system_prompt)
    monkeypatch.setattr(core, "Agent", _FakeAgent)
    monkeypatch.setattr(core, "_agents", {})
    monkeypatch.setattr(
        core.configUtil,
        "get_app_config",
        lambda: AppConfig(
            setting=SettingConfig(llm_services=[], default_llm_server=None, workspace_root=str(tmp_path)),
        ),
    )

    await core.load_team_agents(1, workspace_root=str(tmp_path))

    assert started == [11]
    assert 11 in core._agents
    assert core._agents[11].agent_workdir == f"{tmp_path}/demo"


@pytest.mark.asyncio
async def test_load_team_agents_injects_admin_tools_only_for_top_manager(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    team = SimpleNamespace(id=1, name="demo", config={})
    alice = SimpleNamespace(id=11, team_id=1, name="alice", display_name="alice", role_template_id=21, model="", driver=DriverType.NATIVE, allow_tools=["Category:Read", "Read"], allow_skills=[])
    bob = SimpleNamespace(id=12, team_id=1, name="bob", display_name="bob", role_template_id=22, model="", driver=DriverType.NATIVE, allow_tools=["Category:Read", "Category:Admin", "save_role_template", "Read"], allow_skills=[])
    templates = [
        SimpleNamespace(id=21, name="alice_tpl", display_name="alice_tpl", model=None, soul="alice soul", i18n={}),
        SimpleNamespace(id=22, name="bob_tpl", display_name="bob_tpl", model=None, soul="bob soul", i18n={}),
    ]
    started: list[int] = []

    class _FakeAgent:
        def __init__(
            self,
            *,
            gt_agent: Any,
            system_prompt: str,
            driver_config: Any = None,
            agent_workdir: str = "",
            is_root_leader: bool = False,
        ) -> None:
            self.gt_agent = gt_agent
            self.system_prompt = system_prompt
            self.driver_config = driver_config
            self.agent_workdir = agent_workdir
            self.is_root_leader = is_root_leader

        async def startup(self) -> None:
            started.append(self.gt_agent.id)

    async def _get_team_by_id(team_id: int) -> Any:
        assert team_id == 1
        return team

    async def _get_team_agents(team_id: int, status: Any = None) -> list[Any]:
        assert team_id == 1
        return [alice, bob]

    async def _get_role_templates_by_ids(role_template_ids: list[int]) -> list[Any]:
        assert role_template_ids == [21, 22]
        return templates

    async def _build_agent_system_prompt(**kwargs: Any) -> str:
        return "prompt"

    async def _get_dept_tree(team_id: int) -> Any:
        assert team_id == 1
        return SimpleNamespace(manager_id=11)

    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", _get_team_by_id)
    monkeypatch.setattr(core.gtAgentManager, "get_team_all_agents", _get_team_agents)
    monkeypatch.setattr(core.gtRoleTemplateManager, "get_role_templates_by_ids", _get_role_templates_by_ids)
    monkeypatch.setattr(core.deptService, "get_dept_tree", _get_dept_tree)
    monkeypatch.setattr(core, "build_agent_system_prompt", _build_agent_system_prompt)
    monkeypatch.setattr(core, "Agent", _FakeAgent)
    monkeypatch.setattr(core, "_agents", {})
    monkeypatch.setattr(
        core.configUtil,
        "get_app_config",
        lambda: AppConfig(
            setting=SettingConfig(llm_services=[], default_llm_server=None, workspace_root=str(tmp_path)),
        ),
    )

    await core.load_team_agents(1, workspace_root=str(tmp_path))

    assert started == [11, 12]
    assert core._agents[11].is_root_leader is True
    assert core._agents[12].is_root_leader is False
    assert core._agents[11].driver_config.options["tool_allow_specs"] == ["Category:Read", "Read"]
    assert core._agents[12].driver_config.options["tool_allow_specs"] == [
        "Category:Read",
        "Category:Admin",
        "save_role_template",
        "Read",
    ]
    assert core._agents[11].driver_config.options["is_root_leader"] is True
    assert core._agents[12].driver_config.options["is_root_leader"] is False
