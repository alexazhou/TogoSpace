"""第 7 步：Agent 列表 / 详情的视图模型（设计文档 §8）。

这里钉的是**契约**而不是实现：
- 列表页三字段 `exec_env` / `current_container_id` / `container_count`，未加载的
  Agent 恒为 `host` —— 执行环境只在 Driver 内存里，读不到就不是容器；
- 详情页额外带 `containers`（`ContainerInfo`），前端用 `record_id` 作行 key，
  所以预记录（`docker_id = null`）也必须在列表里；
- 容器数只查 DB，不能在列表页触发 docker 调用（否则 daemon 一慢，整个列表页卡住）；
- 组装结果直接就是 `AgentListInfo` / `AgentDetailInfo`，controller 不再拼字段。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import AgentStatus, AgentTaskStatus, DriverType, EmployStatus, ExecEnv
from model.coreModel import AgentDetailInfo, AgentListInfo, ContainerInfo, ExecEnvState
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


def _container_info(*, record_id: int = 7, docker_id: str | None = "c0ffee123456") -> ContainerInfo:
    return ContainerInfo(
        container=GtAgentContainer(
            id=record_id,
            agent_id=3,
            docker_id=docker_id,
            container_name=f"agent-3-{record_id}",
            image="ubuntu:24.04",
            created_at=datetime(2026, 11, 1, 10, 0, 0),
        ),
        state="exited",
        is_entered=docker_id is not None,
    )


def _patch_detail_deps(
    monkeypatch,
    *,
    exec_state: ExecEnvState,
    containers: list[ContainerInfo],
    first_task=None,
) -> dict:
    """打桩 `get_agent_detail_info` 的外部依赖，返回调用记录。"""
    calls: dict = {}

    monkeypatch.setattr(core, "get_team_runtime_status_map", lambda _team_id: {})
    monkeypatch.setattr(
        core.gtScheculeTaskManager,
        "get_first_unfinish_task",
        AsyncMock(return_value=first_task),
    )
    monkeypatch.setattr(core, "get_exec_env_state", lambda _agent_id: exec_state)

    async def _list(agent_id: int, *, current_container_id=None):
        calls["list_agent_id"] = agent_id
        calls["current_container_id"] = current_container_id
        return containers

    monkeypatch.setattr(core.containerService, "list_agent_containers", _list)
    return calls


# ---------------------------------------------------------------------------
# 详情：exec_env / current_container_id / containers / container_count
# ---------------------------------------------------------------------------


async def test_detail_info_reports_container_env_and_containers(monkeypatch):
    """在容器里时，详情页要如实报告环境、当前容器与容器表。"""
    container = _container_info()
    calls = _patch_detail_deps(
        monkeypatch,
        exec_state=ExecEnvState(exec_env=ExecEnv.CONTAINER, current_container_id="c0ffee123456"),
        containers=[container],
    )

    agent = _agent()
    detail = await core.get_agent_detail_info(agent)

    assert isinstance(detail, AgentDetailInfo)
    assert detail.exec_env == ExecEnv.CONTAINER
    assert detail.current_container_id == "c0ffee123456"
    assert detail.container_count == 1
    assert calls["current_container_id"] == "c0ffee123456"

    dumped = detail.model_dump(mode="json")
    assert dumped["exec_env"] == "container"
    assert dumped["containers"][0]["container"]["record_id"] == 7
    assert dumped["containers"][0]["container"]["docker_id"] == "c0ffee123456"
    assert dumped["containers"][0]["is_entered"] is True
    # `created_at` 必须是 JSON 字符串（datetime 直接进 JSON 会序列化失败）
    assert isinstance(dumped["containers"][0]["container"]["created_at"], str)
    # DB 字段整体包在 `agent` 里，运行时字段在外面
    assert dumped["agent"]["id"] == agent.id
    assert "id" not in dumped
    # Agent 自身时间沿用 jsonUtil 原有格式，不因换成 pydantic 而变成 ISO 的 `T`
    assert dumped["agent"]["created_at"] == agent.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")


async def test_detail_info_includes_pre_records_with_null_docker_id(monkeypatch):
    """预记录（`docker_id = null`）也必须出现在容器表里，并计入 `container_count`。

    预记录是「正在创建」/「创建失败待清理」的唯一入口（只能按 `record_id` 删），
    把它过滤掉用户就没法清理这些记录。
    """
    _patch_detail_deps(
        monkeypatch,
        exec_state=ExecEnvState(),
        containers=[_container_info(record_id=8, docker_id=None)],
    )

    detail = await core.get_agent_detail_info(_agent())

    assert detail.container_count == 1
    assert detail.containers[0].container.docker_id is None
    assert detail.containers[0].is_entered is False


async def test_detail_info_defaults_to_host_without_containers(monkeypatch):
    """没有任何容器时是空列表而不是 None（前端直接 `.length` / 遍历）。"""
    _patch_detail_deps(monkeypatch, exec_state=ExecEnvState(), containers=[])

    detail = await core.get_agent_detail_info(_agent())

    assert detail.exec_env == ExecEnv.HOST
    assert detail.current_container_id is None
    assert detail.containers == []
    assert detail.container_count == 0
    assert detail.model_dump(mode="json")["containers"] == []


async def test_detail_info_carries_failed_task_error_message(monkeypatch):
    """首个未完成任务失败时，error_message 要透出到详情。"""
    failed_task = SimpleNamespace(status=AgentTaskStatus.FAILED, error_message="boom")
    _patch_detail_deps(
        monkeypatch,
        exec_state=ExecEnvState(),
        containers=[],
        first_task=failed_task,
    )

    detail = await core.get_agent_detail_info(_agent())

    assert detail.error_message == "boom"


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

    info = await core.get_team_agent_list_info(1)

    assert isinstance(info, AgentListInfo)
    item = info.agents[0]
    assert item.exec_env == ExecEnv.HOST
    assert item.current_container_id is None
    assert item.container_count == 0
    assert item.status == AgentStatus.IDLE
    assert item.agent.driver == DriverType.TSP
    assert item.agent.employ_status == EmployStatus.ON_BOARD
    assert calls["team_id"] == 1
    assert calls["include_cross_team"] is False

    dumped = info.model_dump(mode="json")
    assert dumped["agents"][0]["exec_env"] == "host"
    assert dumped["agents"][0]["current_container_id"] is None
    # DB 字段整体包在 `agent` 里，运行时字段在外面
    assert dumped["agents"][0]["agent"]["id"] == 3
    assert "id" not in dumped["agents"][0]
    # 枚举对外契约：employ_status / status 用 name，driver 用 value
    assert dumped["agents"][0]["agent"]["employ_status"] == "ON_BOARD"
    assert dumped["agents"][0]["status"] == "IDLE"
    assert dumped["agents"][0]["agent"]["driver"] == "tsp"


async def test_list_info_uses_loaded_state_and_db_count(monkeypatch):
    """已加载的 Agent 用 Driver 内存态 + DB 计数。"""
    _patch_list_deps(
        monkeypatch,
        agents=[_agent()],
        exec_map={3: ExecEnvState(exec_env=ExecEnv.CONTAINER, current_container_id="c0ffee123456")},
        count_map={3: 3},
    )

    info = await core.get_team_agent_list_info(1)

    item = info.agents[0]
    assert item.exec_env == ExecEnv.CONTAINER
    assert item.current_container_id == "c0ffee123456"
    assert item.container_count == 3


async def test_list_info_marks_special_agent_and_forwards_include_special(monkeypatch):
    """特殊团队（team_id=-1）的 Agent 带 `special`；include_special 原样下传。"""
    calls = _patch_list_deps(
        monkeypatch,
        agents=[_agent(agent_id=-1, team_id=-1)],
        exec_map={},
        count_map={},
    )

    info = await core.get_team_agent_list_info(-1, include_special=True)

    assert info.agents[0].special == "operator"
    assert calls["include_cross_team"] is True
