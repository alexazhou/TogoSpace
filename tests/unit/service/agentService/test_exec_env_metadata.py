"""第 7 步：执行环境的读取接口与活动记录元数据（设计文档 §8）。

两件事在这里被钉住：
1. `get_exec_env_state` / `get_team_exec_env_map` **只读 Driver 内存**，未加载恒 host；
2. 活动记录在**开始**和**结束**各写一次环境，因为一个 turn 内部可以
   `enter_container`——只在开始时写，UI 上会出现「在容器里跑的活动显示宿主机」。
"""

from __future__ import annotations

import pytest

from constants import ExecEnv
from model.coreModel import ContainerTarget, ExecEnvState
from service.agentService import core
from service.agentService.agent import Agent
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver.base import AgentDriver, AgentDriverConfig
from constants import DriverType
from model.dbModel.gtAgent import GtAgent
from service.agentActivityService import AgentActivityMeta


class _FakeDriver:
    """只暴露读取环境所需的成员。"""

    def __init__(self, env: ExecEnv = ExecEnv.HOST, container: ContainerTarget | None = None) -> None:
        self.exec_env = env
        self._container = container

    @property
    def current_container(self) -> ContainerTarget | None:
        return self._container

    @property
    def current_container_id(self) -> str | None:
        return self._container.container_id if self._container else None

    def get_exec_env(self) -> ExecEnvState:
        container = self._container
        return ExecEnvState(
            exec_env=self.exec_env,
            current_container_id=self.current_container_id,
            container_name=container.container_name if container else None,
        )


def _default_driver() -> AgentDriver:
    """Native / Claude SDK 使用基类默认实现；这里只需要 `get_exec_env`。"""
    return object.__new__(AgentDriver)


def _fake_agent(agent_id: int, team_id: int, driver) -> Agent:
    agent = Agent(
        gt_agent=GtAgent(
            id=agent_id, team_id=team_id, name=f"agent-{agent_id}", role_template_id=1
        ),
        system_prompt="",
    )
    agent.task_consumer._turn_runner.driver = driver
    return agent


_MANAGED = ContainerTarget(
    container_id="c0ffee123456", is_managed=True, arch="arm64", container_name="agent-3-1"
)


# ---------------------------------------------------------------------------
# get_exec_env_state / get_team_exec_env_map
# ---------------------------------------------------------------------------


def test_exec_env_state_is_host_when_agent_not_loaded(monkeypatch):
    """未加载 ⇒ host / None：环境只存在内存，重启后一律回宿主（不查库猜）。"""
    monkeypatch.setattr(core, "_agents", {})

    assert core.get_exec_env_state(3) == ExecEnvState()


def test_exec_env_state_reports_container_when_loaded(monkeypatch):
    """已加载且在容器里 ⇒ 如实报告容器 ID。"""
    monkeypatch.setattr(
        core, "_agents", {3: _fake_agent(3, 1, _FakeDriver(ExecEnv.CONTAINER, _MANAGED))}
    )

    assert core.get_exec_env_state(3) == ExecEnvState(
        exec_env=ExecEnv.CONTAINER,
        current_container_id="c0ffee123456",
        container_name="agent-3-1",
    )


def test_default_driver_reports_host_env():
    """基类默认实现：不支持容器的驱动恒为 host。"""
    assert _default_driver().get_exec_env() == ExecEnvState()


def test_exec_env_state_for_default_driver_is_host(monkeypatch):
    """Native / Claude SDK 走基类默认实现 ⇒ 恒 host。"""
    monkeypatch.setattr(core, "_agents", {3: _fake_agent(3, 1, _default_driver())})

    assert core.get_exec_env_state(3) == ExecEnvState()


def test_team_exec_env_map_only_contains_loaded_agents_of_that_team(monkeypatch):
    """批量接口只返回本 Team 已加载的 Agent：跨 Team 混进来会让列表页显示别人的环境。"""
    agents = {
        3: _fake_agent(3, 1, _FakeDriver(ExecEnv.CONTAINER, _MANAGED)),
        4: _fake_agent(4, 1, _FakeDriver(ExecEnv.HOST)),
        5: _fake_agent(5, 2, _FakeDriver(ExecEnv.CONTAINER, _MANAGED)),
    }
    monkeypatch.setattr(core, "_agents", agents)

    result = core.get_team_exec_env_map(1)

    assert result == {
        3: ExecEnvState(
            exec_env=ExecEnv.CONTAINER,
            current_container_id="c0ffee123456",
            container_name="agent-3-1",
        ),
        4: ExecEnvState(),
    }


def test_team_exec_env_map_includes_default_driver_as_host(monkeypatch):
    """基类默认实现也要出现在批量结果里，值为 host。"""
    monkeypatch.setattr(
        core, "_agents", {3: _fake_agent(3, 1, _default_driver())}
    )

    assert core.get_team_exec_env_map(1) == {3: ExecEnvState()}


# ---------------------------------------------------------------------------
# 活动记录 metadata：开始写 + 终态回写
# ---------------------------------------------------------------------------


def _make_runner() -> AgentTurnRunner:
    """构造一个 NATIVE driver 的 runner，再把 driver 换成假对象。

    用 NATIVE 起步是为了不依赖 `assets/execute/gtsp/*` 产物（TSP driver 构造时会
    解析二进制路径，缺产物直接抛 FileNotFoundError），这些用例本来也只关心
    「`_base_metadata` 从 driver 读到什么」。
    """
    return AgentTurnRunner(
        gt_agent=GtAgent(id=3, team_id=1, name="小美", role_template_id=2),
        system_prompt="",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )


def test_base_metadata_carries_exec_env_fields_from_driver():
    """`_base_metadata` 是活动记录的唯一入口，环境三字段必须在这里自动带上。"""
    runner = _make_runner()
    runner.driver = _FakeDriver(ExecEnv.CONTAINER, _MANAGED)  # type: ignore[assignment]

    meta = runner._base_metadata()

    assert meta.exec_env == "container"
    assert meta.current_container_id == "c0ffee123456"
    assert meta.container_name == "agent-3-1"


def test_base_metadata_keeps_host_state_without_container():
    """宿主态：`exec_env=host`、容器字段为 None（`to_dict()` 会把 None 剔掉）。"""
    runner = _make_runner()
    runner.driver = _FakeDriver(ExecEnv.HOST)  # type: ignore[assignment]

    meta = runner._base_metadata()

    assert meta.exec_env == "host"
    assert meta.current_container_id is None
    assert meta.container_name is None


def test_base_metadata_reports_host_for_native_driver():
    """Native driver 走基类默认实现，活动记录如实写 `host`，不伪造容器字段。"""
    meta = _make_runner()._base_metadata()

    assert meta.exec_env == "host"
    assert meta.current_container_id is None
    assert meta.container_name is None


def test_merge_exec_env_overlays_fields_without_touching_others():
    """`merge_exec_env` 只覆盖环境三字段，其它 metadata 原样保留。"""
    meta = AgentActivityMeta(tool_result={"success": True}).merge_exec_env(
        ExecEnvState(
            exec_env=ExecEnv.CONTAINER,
            current_container_id="c0ffee123456",
            container_name="agent-3-1",
        )
    )

    assert meta.exec_env == "container"
    assert meta.current_container_id == "c0ffee123456"
    assert meta.container_name == "agent-3-1"
    assert meta.tool_result == {"success": True}


def test_merge_exec_env_keeps_fields_empty_when_state_is_none():
    """state 为 None ⇒ 环境字段保持为空（调用方没有可用快照）。"""
    meta = AgentActivityMeta().merge_exec_env(None)

    assert meta.exec_env is None
    assert meta.current_container_id is None
    assert meta.container_name is None


@pytest.mark.asyncio
async def test_finish_activity_reports_current_env_at_finish_time(monkeypatch):
    """`_finish_activity` 真的把环境透传给 `update_activity_progress`。

    这条是「结束时 patch 为最终环境」的落地检查：只测 `merge_exec_env` 的话，
    忘记接到 `_finish_activity` 上仍然会绿。
    """
    runner = _make_runner()
    runner.driver = _FakeDriver(ExecEnv.CONTAINER, _MANAGED)  # type: ignore[assignment]

    captured: dict = {}

    async def _update(activity_id, *, status=None, detail=None, error_message=None, metadata_patch=None):
        captured.update(
            activity_id=activity_id,
            status=status,
            metadata_patch=metadata_patch,
        )

    monkeypatch.setattr(
        "service.agentService.agentTurnRunner.agentActivityService.update_activity_progress", _update
    )

    from constants import AgentActivityStatus

    await runner._finish_activity(11, status=AgentActivityStatus.SUCCEEDED)

    assert captured["activity_id"] == 11
    patch = captured["metadata_patch"]
    assert patch.exec_env == "container"
    assert patch.current_container_id == "c0ffee123456"
