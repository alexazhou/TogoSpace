"""第 6 步：目录变更 / 离职时的容器清理（设计文档 §7）。

这一层的测试重点不是「容器有没有被删干净」，而是**清理时机与调用顺序**：
- 必须在目录移动**之前**清理（bind mount 在创建时固化绝对路径）；
- 必须先切回宿主再删，否则 rm 会把正在用的 stdio 连接打断；
- 清理是 best-effort，**任何清理失败都不能阻塞改名/离职**（否则一次 docker
  抽风就能让用户改不了成员名）；
- 不该清理的时候不能清（自定义 working_directory 的 Team 改名、只改其它配置）。

`containerService` 在这里一律被 monkeypatch 掉：本步骤验证的是调用编排，
容器层的真实语义已在 `tests/unit/service/containerService/` 用假 docker 覆盖。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from constants import DriverType, EmployStatus, ExecEnv
from model.coreModel import ExecEnvSnapshot
from model.dbModel.gtAgent import GtAgent
from service.agentService import core
from service.agentService.agent import Agent
from service import teamService
from service.containerService import core as containerCore
from util.configUtil.configTypes import AppConfig, SettingConfig


def _app_config(workspace_root) -> AppConfig:
    return AppConfig(
        setting=SettingConfig(
            llm_services=[],
            default_llm_server=None,
            workspace_root=str(workspace_root),
        ),
    )


@asynccontextmanager
async def _noop_atomic():
    yield


def _make_agent(agent_id: int, name: str, team_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=agent_id,
        team_id=team_id,
        name=name,
        role_template_id=21,
        model="",
        driver=DriverType.NATIVE,
        employ_status=EmployStatus.ON_BOARD,
        i18n={},
        allow_tools=None,
    )


class _FakeDriver:
    """最小 driver 桩：只暴露清理路径用到的那几个成员。"""

    def __init__(self, env: ExecEnv = ExecEnv.HOST, container_id: str | None = None) -> None:
        self.exec_env = env
        self.current_container_id = container_id
        self.disconnect = AsyncMock()
        self.connect = AsyncMock(return_value=True)
        self.is_connected = Mock(return_value=True)

    def get_exec_env(self) -> ExecEnvSnapshot:
        return ExecEnvSnapshot(
            exec_env=self.exec_env,
            current_container_id=self.current_container_id,
        )


def _fake_agent(driver) -> Agent:
    agent = Agent(
        gt_agent=GtAgent(id=1, team_id=1, name="小美", role_template_id=1),
        system_prompt="",
    )
    agent.task_consumer._turn_runner.driver = driver
    return agent


# ---------------------------------------------------------------------------
# switch_agent_to_host：清理前必须先回宿主
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_to_host_is_noop_when_agent_not_loaded(monkeypatch):
    """未加载的 Agent 没有连接，无需切换，直接视为已在宿主。"""
    monkeypatch.setattr(core, "_agents", {})

    assert await core.switch_agent_to_host(999) is True


@pytest.mark.asyncio
async def test_switch_to_host_is_noop_when_already_host(monkeypatch):
    """已经在宿主时不能重连：重连会打断正在进行的 turn。"""
    driver = _FakeDriver(env=ExecEnv.HOST)
    monkeypatch.setattr(core, "_agents", {1: _fake_agent(driver)})

    assert await core.switch_agent_to_host(1) is True
    driver.disconnect.assert_not_awaited()
    driver.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_switch_to_host_disconnects_then_connects_host(monkeypatch):
    """在容器里时，必须是「先 disconnect 再 connect(HOST)」这个顺序。"""
    driver = _FakeDriver(env=ExecEnv.CONTAINER, container_id="c0ffee123456")
    monkeypatch.setattr(core, "_agents", {1: _fake_agent(driver)})

    assert await core.switch_agent_to_host(1) is True
    driver.disconnect.assert_awaited_once()
    driver.connect.assert_awaited_once_with(ExecEnv.HOST)


@pytest.mark.asyncio
async def test_switch_to_host_swallows_connect_failure(monkeypatch):
    """回宿主失败不抛异常：清理仍要继续（容器删掉后连接反正也失效了）。"""
    driver = _FakeDriver(env=ExecEnv.CONTAINER, container_id="c0ffee123456")
    driver.connect.return_value = False
    monkeypatch.setattr(core, "_agents", {1: _fake_agent(driver)})

    assert await core.switch_agent_to_host(1) is False


@pytest.mark.asyncio
async def test_switch_to_host_swallows_driver_exception(monkeypatch):
    """driver 抛异常也不能冒泡：这是收尾动作，不能把目录操作带崩。"""
    driver = _FakeDriver(env=ExecEnv.CONTAINER, container_id="c0ffee123456")
    driver.disconnect.side_effect = RuntimeError("stdio boom")
    monkeypatch.setattr(core, "_agents", {1: _fake_agent(driver)})

    assert await core.switch_agent_to_host(1) is False


# ---------------------------------------------------------------------------
# overwrite_team_agents：改名 / 离职
# ---------------------------------------------------------------------------


def _mock_overwrite(monkeypatch, team, agents, save_agents, cleaned: list[int]):
    async def _get_team(_team_id: int):
        return team

    async def _get_agents(_team_id: int, status=None):
        return agents

    async def _clean(agent_id: int) -> int:
        cleaned.append(agent_id)
        return 1

    monkeypatch.setattr(core.gtTeamManager, "get_team_by_id", _get_team)
    monkeypatch.setattr(core.gtAgentManager, "get_team_all_agents", _get_agents)
    monkeypatch.setattr(core.gtAgentManager, "batch_save_agents", save_agents)
    monkeypatch.setattr(core.gtAgentManager, "batch_update_agent_status", AsyncMock())
    monkeypatch.setattr(core, "atomic", _noop_atomic)
    monkeypatch.setattr(core, "switch_agent_to_host", AsyncMock(return_value=True))
    monkeypatch.setattr(core.containerService, "delete_agent_containers_safely", _clean)
    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config("/tmp/ws"))


@pytest.mark.asyncio
async def test_rename_cleans_containers_before_moving_dir(monkeypatch, tmp_path):
    """改名成员时，容器清理必须发生在目录移动之前（§7）。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    cleaned: list[int] = []
    _mock_overwrite(monkeypatch, team, [alice], AsyncMock(), cleaned)

    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))
    (tmp_path / "demo" / "agent" / "alice").mkdir(parents=True)

    order: list[str] = []
    real_rename = core.workspaceUtil.rename_agent_workdirs

    def _record_rename(*args, **kwargs):
        order.append("rename_dir")
        return real_rename(*args, **kwargs)

    async def _record_switch(agent_id: int) -> bool:
        order.append(f"switch:{agent_id}")
        return True

    async def _record_clean(agent_id: int) -> int:
        order.append(f"clean:{agent_id}")
        return 1

    monkeypatch.setattr(core.workspaceUtil, "rename_agent_workdirs", _record_rename)
    monkeypatch.setattr(core, "switch_agent_to_host", _record_switch)
    monkeypatch.setattr(core.containerService, "delete_agent_containers_safely", _record_clean)

    payload = [GtAgent(id=11, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE)]
    await core.overwrite_team_agents(1, payload)

    # 顺序不可反：先切回宿主，再删容器，最后才移动目录（§7）
    assert order == ["switch:11", "clean:11", "rename_dir"]


@pytest.mark.asyncio
async def test_rename_does_not_clean_containers_when_name_unchanged(monkeypatch, tmp_path):
    """名字没变就不清容器：只是改角色/模型不该把用户的沙箱删掉。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    cleaned: list[int] = []
    _mock_overwrite(monkeypatch, team, [alice], AsyncMock(), cleaned)

    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))
    (tmp_path / "demo" / "agent" / "alice").mkdir(parents=True)

    payload = [GtAgent(id=11, team_id=1, name="alice", role_template_id=21, model="gpt-x", driver=DriverType.NATIVE)]
    await core.overwrite_team_agents(1, payload)

    assert cleaned == []


@pytest.mark.asyncio
async def test_offboard_cleans_containers_of_removed_agent(monkeypatch, tmp_path):
    """离职成员的容器要在删目录前清掉。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    bob = _make_agent(12, "bob")
    cleaned: list[int] = []
    _mock_overwrite(monkeypatch, team, [alice, bob], AsyncMock(), cleaned)

    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))
    (tmp_path / "demo" / "agent" / "alice").mkdir(parents=True)
    (tmp_path / "demo" / "agent" / "bob").mkdir(parents=True)

    payload = [GtAgent(id=11, team_id=1, name="alice", role_template_id=21, model="", driver=DriverType.NATIVE)]
    await core.overwrite_team_agents(1, payload)

    assert cleaned == [12]


@pytest.mark.asyncio
async def test_offboard_cleanup_happens_after_db_commit(monkeypatch, tmp_path):
    """写库失败时不能清理容器：此时成员并没离职，清掉就把他的沙箱误删了。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    bob = _make_agent(12, "bob")
    cleaned: list[int] = []
    _mock_overwrite(monkeypatch, team, [alice, bob], AsyncMock(side_effect=RuntimeError("db down")), cleaned)

    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))
    (tmp_path / "demo" / "agent" / "alice").mkdir(parents=True)
    (tmp_path / "demo" / "agent" / "bob").mkdir(parents=True)

    payload = [GtAgent(id=11, team_id=1, name="alice", role_template_id=21, model="", driver=DriverType.NATIVE)]
    with pytest.raises(RuntimeError, match="db down"):
        await core.overwrite_team_agents(1, payload)

    assert cleaned == []


@pytest.mark.asyncio
async def test_rename_and_offboard_are_both_cleaned(monkeypatch, tmp_path):
    """同一批里既有改名又有离职：两个 Agent 都要清，且各自只清一次。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=False)
    alice = _make_agent(11, "alice")
    bob = _make_agent(12, "bob")
    carol = _make_agent(13, "carol")
    cleaned: list[int] = []
    _mock_overwrite(monkeypatch, team, [alice, bob, carol], AsyncMock(), cleaned)

    monkeypatch.setattr(core.configUtil, "get_app_config", lambda: _app_config(tmp_path))
    for name in ("alice", "bob", "carol"):
        (tmp_path / "demo" / "agent" / name).mkdir(parents=True)

    payload = [
        GtAgent(id=11, team_id=1, name="alice2", role_template_id=21, model="", driver=DriverType.NATIVE),
        GtAgent(id=12, team_id=1, name="bob", role_template_id=21, model="", driver=DriverType.NATIVE),
    ]
    await core.overwrite_team_agents(1, payload)

    assert cleaned == [11, 13]


# ---------------------------------------------------------------------------
# teamService：Team 改名 / 工作目录变更 / 删除
# ---------------------------------------------------------------------------


def _mock_team_service(monkeypatch, team, cleaned: list[int]):
    async def _get_team_by_id(_team_id: int):
        return team

    async def _save_team(saved):
        return saved

    async def _clean(team_id: int) -> int:
        cleaned.append(team_id)
        return 1

    monkeypatch.setattr(teamService.gtTeamManager, "get_team_by_id", _get_team_by_id)
    monkeypatch.setattr(teamService.gtTeamManager, "get_team", AsyncMock(return_value=None))
    monkeypatch.setattr(teamService.gtTeamManager, "save_team", _save_team)
    monkeypatch.setattr(teamService.agentService, "switch_team_agents_to_host", AsyncMock())
    monkeypatch.setattr(teamService.containerService, "delete_team_containers_safely", _clean)
    monkeypatch.setattr(teamService.workspaceUtil.configUtil, "get_app_config", lambda: _app_config("/tmp/ws"))


@pytest.mark.asyncio
async def test_rename_team_cleans_containers_before_moving_dir(monkeypatch, tmp_path):
    """Team 改名（托管目录）要清容器，且清理发生在目录移动之前。"""
    team = SimpleNamespace(id=1, name="old", config={}, enabled=True)
    cleaned: list[int] = []
    _mock_team_service(monkeypatch, team, cleaned)
    monkeypatch.setattr(teamService.workspaceUtil.configUtil, "get_app_config", lambda: _app_config(tmp_path))

    order: list[str] = []
    real_move = teamService.workspaceUtil.rename_team_workdir

    def _record_move(*args, **kwargs):
        order.append("move_dir")
        return real_move(*args, **kwargs)

    async def _record_clean(team_id: int) -> int:
        order.append(f"clean:{team_id}")
        return 1

    monkeypatch.setattr(teamService.workspaceUtil, "rename_team_workdir", _record_move)
    monkeypatch.setattr(teamService.containerService, "delete_team_containers_safely", _record_clean)

    (tmp_path / "old" / "shared").mkdir(parents=True)

    await teamService.rename_team(1, "new")

    assert order == ["clean:1", "move_dir"]


@pytest.mark.asyncio
async def test_rename_team_skips_cleanup_for_custom_working_directory(monkeypatch, tmp_path):
    """自定义 working_directory 的 Team 改名不移动目录，容器仍有效，不该清。"""
    team = SimpleNamespace(
        id=1, name="old", config={"working_directory": str(tmp_path / "custom")}, enabled=True
    )
    cleaned: list[int] = []
    _mock_team_service(monkeypatch, team, cleaned)

    await teamService.rename_team(1, "new")

    assert cleaned == []


@pytest.mark.asyncio
async def test_update_working_directory_cleans_containers(monkeypatch, tmp_path):
    """改工作目录会让旧容器的挂载路径失效，必须清理。"""
    team = SimpleNamespace(id=1, name="demo", config={}, enabled=True)
    cleaned: list[int] = []
    _mock_team_service(monkeypatch, team, cleaned)

    await teamService.update_team_base_info(1, working_directory=str(tmp_path / "new_root"))

    assert cleaned == [1]


@pytest.mark.asyncio
async def test_update_other_config_does_not_clean_containers(monkeypatch, tmp_path):
    """只改其它配置（工作目录没变）时不清容器。"""
    team = SimpleNamespace(id=1, name="demo", config={"working_directory": str(tmp_path / "keep")}, enabled=True)
    cleaned: list[int] = []
    _mock_team_service(monkeypatch, team, cleaned)
    (tmp_path / "keep").mkdir(parents=True, exist_ok=True)

    await teamService.update_team_base_info(1, config_updates={"foo": "bar"})

    assert cleaned == []


@pytest.mark.asyncio
async def test_delete_team_cleans_containers_after_runtime_stopped(monkeypatch):
    """Team 删除：先停运行时（断开 stdio，容器自然停回），再清理容器记录。"""
    team = SimpleNamespace(id=7, name="demo", config={}, enabled=True)
    order: list[str] = []
    cleaned: list[int] = []

    async def _get_team(_name: str):
        return team

    async def _stop(_team_id: int):
        order.append("stop_runtime")

    async def _clean(team_id: int) -> int:
        order.append(f"clean:{team_id}")
        cleaned.append(team_id)
        return 1

    async def _delete(_name: str):
        order.append("delete_db")

    monkeypatch.setattr(teamService.gtTeamManager, "get_team", _get_team)
    monkeypatch.setattr(teamService.gtTeamManager, "delete_team", _delete)
    monkeypatch.setattr(teamService, "stop_team_runtime", _stop)
    monkeypatch.setattr(teamService.agentService, "switch_team_agents_to_host", AsyncMock())
    monkeypatch.setattr(teamService.containerService, "delete_team_containers_safely", _clean)

    await teamService.delete_team("demo")

    assert order == ["stop_runtime", "clean:7", "delete_db"]


@pytest.mark.asyncio
async def test_delete_team_deletes_db_even_when_cleanup_raises(monkeypatch):
    """容器清理异常由 containerService 吞掉，不能阻止 Team 从库里删除。"""
    team = SimpleNamespace(id=7, name="demo", config={}, enabled=True)
    deleted: list[str] = []

    async def _get_team(_name: str):
        return team

    async def _delete(name: str):
        deleted.append(name)

    monkeypatch.setattr(teamService.gtTeamManager, "get_team", _get_team)
    monkeypatch.setattr(teamService.gtTeamManager, "delete_team", _delete)
    monkeypatch.setattr(teamService, "stop_team_runtime", AsyncMock())
    monkeypatch.setattr(teamService.agentService, "switch_team_agents_to_host", AsyncMock())
    monkeypatch.setattr(
        containerCore, "delete_team_containers",
        AsyncMock(side_effect=RuntimeError("docker gone")),
    )

    await teamService.delete_team("demo")

    assert deleted == ["demo"]


@pytest.mark.asyncio
async def test_switch_team_agents_to_host_switches_all_loaded_agents(monkeypatch):
    """Team 级清理前要把该 Team 已加载的每个 Agent 都切回宿主。"""
    order: list[str] = []

    def _make_recording_driver(container_id: str) -> _FakeDriver:
        driver = _FakeDriver(env=ExecEnv.CONTAINER, container_id=container_id)

        async def _disconnect() -> None:
            order.append("disconnect")

        async def _connect(_env) -> bool:
            order.append("connect_host")
            return True

        # 必须改实例上的 AsyncMock（_FakeDriver.__init__ 已把它们挂到实例上），
        # 在子类里重写同名方法会被实例属性遮蔽。
        driver.disconnect.side_effect = _disconnect
        driver.connect.side_effect = _connect
        return driver

    drivers = {1: _make_recording_driver("c0ffee123456"), 2: _make_recording_driver("deadbeef0002")}
    monkeypatch.setattr(core, "_agents", {aid: _fake_agent(d) for aid, d in drivers.items()})

    await core.switch_team_agents_to_host(1)

    assert order == ["disconnect", "connect_host", "disconnect", "connect_host"]


@pytest.mark.asyncio
async def test_switch_team_agents_to_host_defaults_to_loaded_agents(monkeypatch):
    """不传 agent_ids 时只处理**已加载**的成员，且不查库。

    切回宿主只对已加载的 Agent 有意义；Team 改名这条路径因此完全不依赖 DB。
    """
    order: list[str] = []

    def _make_recording_driver(container_id: str) -> _FakeDriver:
        driver = _FakeDriver(env=ExecEnv.CONTAINER, container_id=container_id)

        async def _disconnect() -> None:
            order.append("disconnect")

        async def _connect(_env) -> bool:
            order.append("connect_host")
            return True

        driver.disconnect.side_effect = _disconnect
        driver.connect.side_effect = _connect
        return driver

    loaded = _fake_agent(_make_recording_driver("c0ffee123456"))
    loaded.gt_agent.team_id = 3
    other_team = _fake_agent(_FakeDriver())
    other_team.gt_agent.team_id = 99
    monkeypatch.setattr(core, "_agents", {5: loaded, 6: other_team})

    def _no_db(*_args, **_kwargs):
        raise AssertionError("不应在此路径查库")

    monkeypatch.setattr(core.gtAgentManager, "get_team_all_agents", _no_db)

    await core.switch_team_agents_to_host(3)

    assert order == ["disconnect", "connect_host"]
