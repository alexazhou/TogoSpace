from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.agentService import core
from util.configTypes import AppConfig, SettingConfig
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
async def test_unload_team_removes_only_target_team_runtime(monkeypatch):
    target = _DummyAgent(team_id=1)
    other = _DummyAgent(team_id=2)
    monkeypatch.setattr(core, "_agents", {11: target, 22: other})

    await core.unload_team(1)

    assert target.closed is True
    assert other.closed is False
    assert 11 not in core._agents
    assert 22 in core._agents


@pytest.mark.asyncio
async def test_load_team_agents_delegates_to_internal_loader(monkeypatch):
    mock_load_team_agents = AsyncMock()
    monkeypatch.setattr(core, "_load_team_agents", mock_load_team_agents)

    await core.load_team_agents(1, workspace_root="/tmp/ws")

    mock_load_team_agents.assert_awaited_once_with(1, workspace_root="/tmp/ws")


def test_resolve_team_workdir_prefers_explicit_working_directory():
    team = SimpleNamespace(name="default", config={"working_directory": "/tmp/custom-team-dir"})

    resolved = core._resolve_team_workdir(team, "/tmp/workspaces")

    assert resolved == "/tmp/custom-team-dir"


def test_resolve_team_workdir_falls_back_to_workspace_root():
    team = SimpleNamespace(name="default", config={})

    resolved = core._resolve_team_workdir(team, "/tmp/workspaces")

    assert resolved == "/tmp/workspaces/default"


def test_agent_model_resolution_logic():
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
async def test_load_team_agents_allows_startup_without_available_llm(monkeypatch, tmp_path):
    team = SimpleNamespace(id=1, name="demo", config={})
    gt_agent = SimpleNamespace(id=11, team_id=1, name="alice", role_template_id=21, model="", driver=DriverType.NATIVE)
    template = SimpleNamespace(id=21, name="alice", model=None, soul="mock soul", allowed_tools=None)
    started: list[int] = []

    class _FakeAgent:
        def __init__(self, *, gt_agent, system_prompt, driver_config=None, agent_workdir="") -> None:
            self.gt_agent = gt_agent
            self.system_prompt = system_prompt
            self.driver_config = driver_config
            self.agent_workdir = agent_workdir

        async def startup(self) -> None:
            started.append(self.gt_agent.id)

    async def _get_team_by_id(team_id: int):
        assert team_id == 1
        return team

    async def _get_team_agents(team_id: int):
        assert team_id == 1
        return [gt_agent]

    async def _get_role_templates_by_ids(role_template_ids: list[int]):
        assert role_template_ids == [21]
        return [template]

    async def _build_agent_system_prompt(**kwargs):
        return "prompt"

    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", _get_team_by_id)
    monkeypatch.setattr(core.gtAgentManager, "get_team_agents", _get_team_agents)
    monkeypatch.setattr(core.gtRoleTemplateManager, "get_role_templates_by_ids", _get_role_templates_by_ids)
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
