"""第 7 步：Agent 列表 / 详情的视图模型（设计文档 §8）。

这里钉的是**契约**而不是实现：
- 列表页带 `exec_env_state` / `container_count`，未加载的 Agent 恒为 `host`；
- 容器列表走独立接口（`get_agent_containers`），不再塞进 Agent 详情；
- 容器数只查 DB，不能在列表页触发 docker 调用（否则 daemon 一慢，整个列表页卡住）；
- 列表 service 返回 `list[AgentRuntimeInfo]`，响应外壳由 controller 组装；
- 失败信息走独立接口（`get_agent_error_message`），不混进 Agent 视图。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import (
    AgentStatus,
    AgentTaskStatus,
    ContainerState,
    DriverType,
    EmployStatus,
    ExecEnv,
)
from model.coreModel import AgentRuntimeInfo, ContainerRuntimeInfo, ExecEnvState
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentContainer import GtAgentContainer
from service.agentService import core


def _agent(agent_id: int = 3, team_id: int = 1) -> GtAgent:
    return GtAgent(
        id=agent_id,
        team_id=team_id,
        name="小美",
        role_template_id=2,
        employ_status=EmployStatus.ON_BOARD,
        driver=DriverType.TSP,
        i18n={"display_name": "小美"},
    )


def _container_info(*, record_id: int = 7, container_id: str | None = "c0ffee123456") -> ContainerRuntimeInfo:
    return ContainerRuntimeInfo(
        record=GtAgentContainer(
            id=record_id,
            agent_id=3,
            container_id=container_id,
            container_name=f"agent-3-{record_id}",
            image="ubuntu:24.04",
            created_at=datetime(2026, 11, 1, 10, 0, 0),
            updated_at=datetime(2026, 11, 1, 10, 0, 0),
        ),
        state=ContainerState.EXITED,
        is_entered=container_id is not None,
    )


def _patch_detail_deps(
    monkeypatch,
    *,
    exec_state: ExecEnvState,
    count_map: dict[int, int] | None = None,
) -> None:
    """打桩 `get_agent_detail_info` 的外部依赖。"""
    monkeypatch.setattr(core, "get_team_runtime_status_map", lambda _team_id: {})
    monkeypatch.setattr(core, "get_exec_env_state", lambda _agent_id: exec_state)
    monkeypatch.setattr(
        core.containerService,
        "count_containers_by_agent_ids",
        AsyncMock(return_value=count_map or {}),
    )


# ---------------------------------------------------------------------------
# 详情：exec_env_state / container_count
# ---------------------------------------------------------------------------


async def test_detail_info_reports_container_env(monkeypatch):
    """在容器里时，详情页要如实报告执行环境与当前容器。"""
    _patch_detail_deps(
        monkeypatch,
        exec_state=ExecEnvState(
            exec_env=ExecEnv.CONTAINER,
            current_container_id="c0ffee123456",
            container_name="agent-3-7",
        ),
        count_map={3: 1},
    )

    agent = _agent()
    detail = await core.get_agent_detail_info(agent)

    assert isinstance(detail, AgentRuntimeInfo)
    assert detail.exec_env_state.exec_env == ExecEnv.CONTAINER
    assert detail.exec_env_state.current_container_id == "c0ffee123456"
    # container_name 供活动记录 / 前端展示使用，随接口一起返回
    assert detail.exec_env_state.container_name == "agent-3-7"
    assert detail.container_count == 1

    dumped = detail.model_dump(mode="json")
    assert dumped["exec_env_state"] == {
        "exec_env": "container",
        "current_container_id": "c0ffee123456",
        "container_name": "agent-3-7",
    }
    # 容器列表 / 失败信息都走独立接口，详情不再内嵌
    assert "containers" not in dumped
    assert "error_message" not in dumped
    # DB 字段整体包在 `agent` 里，运行时字段在外面
    assert dumped["agent"]["id"] == agent.id
    assert "id" not in dumped
    # Agent 自身时间沿用 jsonUtil 原有格式，不因换成 pydantic 而变成 ISO 的 `T`
    assert dumped["agent"]["created_at"] == agent.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")


async def test_detail_info_defaults_to_host(monkeypatch):
    """未加载 / 未进入容器时执行环境回落到宿主，容器数为 0。"""
    _patch_detail_deps(monkeypatch, exec_state=ExecEnvState())

    detail = await core.get_agent_detail_info(_agent())

    assert detail.exec_env_state.exec_env == ExecEnv.HOST
    assert detail.exec_env_state.current_container_id is None
    assert detail.container_count == 0
    assert detail.model_dump(mode="json")["exec_env_state"] == {
        "exec_env": "host",
        "current_container_id": None,
        "container_name": None,
    }


# ---------------------------------------------------------------------------
# 独立失败信息接口
# ---------------------------------------------------------------------------


async def test_get_agent_error_message_returns_failed_task_message(monkeypatch):
    """最早未完成任务失败时返回其 error_message。"""
    failed_task = SimpleNamespace(status=AgentTaskStatus.FAILED, error_message="boom")
    monkeypatch.setattr(
        core.gtScheculeTaskManager,
        "get_first_unfinish_task",
        AsyncMock(return_value=failed_task),
    )

    assert await core.get_agent_error_message(3) == "boom"


async def test_get_agent_error_message_returns_none_without_failure(monkeypatch):
    """没有未完成任务 / 最早任务未失败时返回 None。"""
    for task in (None, SimpleNamespace(status=AgentTaskStatus.PENDING, error_message="")):
        monkeypatch.setattr(
            core.gtScheculeTaskManager,
            "get_first_unfinish_task",
            AsyncMock(return_value=task),
        )
        assert await core.get_agent_error_message(3) is None


# ---------------------------------------------------------------------------
# 独立容器接口：列表不再塞进 Agent 详情
# ---------------------------------------------------------------------------


async def test_get_agent_containers_forwards_current_container(monkeypatch):
    """容器列表独立查询，并把当前进入的容器 ID 交给 containerService。"""
    calls: dict = {}

    async def _list(agent_id: int, *, current_container_id=None):
        calls["agent_id"] = agent_id
        calls["current_container_id"] = current_container_id
        return [_container_info()]

    monkeypatch.setattr(
        core,
        "get_exec_env_state",
        lambda _agent_id: ExecEnvState(
            exec_env=ExecEnv.CONTAINER, current_container_id="c0ffee123456"
        ),
    )
    monkeypatch.setattr(core.containerService, "list_agent_containers", _list)

    containers = await core.get_agent_containers(3)

    assert [item.record.id for item in containers] == [7]
    assert calls == {"agent_id": 3, "current_container_id": "c0ffee123456"}


# ---------------------------------------------------------------------------
# 列表：三字段 + 未加载回落 host
# ---------------------------------------------------------------------------


def _patch_list_deps(monkeypatch, *, agents: list[GtAgent], exec_map, count_map) -> dict:
    calls: dict = {}

    async def _agents(team_id: int, *, include_cross_team: bool = False):
        calls["team_id"] = team_id
        calls["include_cross_team"] = include_cross_team
        return agents

    monkeypatch.setattr(core.gtAgentManager, "get_team_all_agents", _agents)
    monkeypatch.setattr(core, "get_team_runtime_status_map", lambda _team_id: {})
    monkeypatch.setattr(core, "get_team_exec_env_map", lambda _team_id: exec_map)
    monkeypatch.setattr(
        core.containerService,
        "count_containers_by_agent_ids",
        AsyncMock(return_value=count_map),
    )
    return calls


async def test_list_info_falls_back_to_host_for_unloaded_agent(monkeypatch):
    """未加载的 Agent 不在 `exec_env_map` 里 ⇒ 一律 host、count 0。

    不能假设「没查到就是在容器」：环境只在内存，后端重启后全部回宿主。
    """
    calls = _patch_list_deps(monkeypatch, agents=[_agent()], exec_map={}, count_map={})

    items = await core.get_team_agent_list_info(1)

    assert isinstance(items, list)
    assert isinstance(items[0], AgentRuntimeInfo)
    item = items[0]
    assert item.exec_env_state.exec_env == ExecEnv.HOST
    assert item.exec_env_state.current_container_id is None
    assert item.container_count == 0
    assert item.status == AgentStatus.IDLE
    assert item.agent.driver == DriverType.TSP
    assert item.agent.employ_status == EmployStatus.ON_BOARD
    assert calls["team_id"] == 1
    assert calls["include_cross_team"] is False

    dumped = item.model_dump(mode="json")
    assert dumped["exec_env_state"] == {
        "exec_env": "host",
        "current_container_id": None,
        "container_name": None,
    }
    # DB 字段整体包在 `agent` 里，运行时字段在外面
    assert dumped["agent"]["id"] == 3
    assert "id" not in dumped
    # 枚举对外契约：employ_status / status 用 name，driver 用 value
    assert dumped["agent"]["employ_status"] == "ON_BOARD"
    assert dumped["status"] == "IDLE"
    assert dumped["agent"]["driver"] == "tsp"


async def test_list_info_uses_loaded_state_and_db_count(monkeypatch):
    """已加载的 Agent 用 Driver 内存态 + DB 计数。"""
    _patch_list_deps(
        monkeypatch,
        agents=[_agent()],
        exec_map={3: ExecEnvState(exec_env=ExecEnv.CONTAINER, current_container_id="c0ffee123456")},
        count_map={3: 3},
    )

    items = await core.get_team_agent_list_info(1)

    item = items[0]
    assert item.exec_env_state.exec_env == ExecEnv.CONTAINER
    assert item.exec_env_state.current_container_id == "c0ffee123456"
    assert item.container_count == 3


async def test_list_info_forwards_include_special_without_extra_flag(monkeypatch):
    """include_special 只控制是否带出 `team_id=-1` 的记录，不再额外派发 `special`。"""
    calls = _patch_list_deps(
        monkeypatch,
        agents=[_agent(agent_id=-1, team_id=-1)],
        exec_map={},
        count_map={},
    )

    items = await core.get_team_agent_list_info(-1, include_special=True)

    assert calls["include_cross_team"] is True
    # 特殊身份由 agent 自身的 id / team_id 表达，外层不重复一份
    dumped = items[0].model_dump(mode="json")
    assert "special" not in dumped
    assert dumped["agent"]["id"] == -1
    assert dumped["agent"]["team_id"] == -1
