"""第 7 步：Agent 列表 / 详情接口的执行环境与容器字段（设计文档 §8）。

这里钉的是**契约**而不是实现：
- 列表页三字段 `exec_env` / `current_container_id` / `container_count`，未加载的
  Agent 恒为 `host` —— 执行环境只在 Driver 内存里，读不到就不是容器；
- 详情页额外带 `containers`（`ContainerInfo.model_dump(mode="json")`），前端用
  `record_id` 作行 key，所以预记录（`docker_id = null`）也必须在列表里；
- 容器数只查 DB，不能在列表页触发 docker 调用（否则 daemon 一慢，整个列表页卡住）。

handler 用 `object.__new__` 直接构造：只需要 `request` 与 `return_json` 两个入口，
走完整 tornado 生命周期反而要把连接、cookie 都造出来，测不到额外的东西。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import AgentStatus, DriverType, EmployStatus, ExecEnv
from controller import agentController
from model.coreModel import ContainerInfo
from model.dbModel.gtAgent import GtAgent


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
        record_id=record_id,
        agent_id=3,
        docker_id=docker_id,
        container_name=f"agent-3-{record_id}",
        image="ubuntu:24.04",
        state="exited",
        is_entered=docker_id is not None,
        created_at=datetime(2026, 11, 1, 10, 0, 0),
    )


def _patch_detail_deps(
    monkeypatch,
    *,
    exec_state: dict,
    containers: list[ContainerInfo],
) -> dict:
    """打桩 `_build_agent_detail_payload` 的外部依赖，返回调用记录。"""
    calls: dict = {}

    monkeypatch.setattr(
        agentController.agentService,
        "get_team_runtime_status_map",
        lambda _team_id: {},
    )
    monkeypatch.setattr(
        agentController.gtScheculeTaskManager,
        "get_first_unfinish_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        agentController.agentService,
        "get_exec_env_state",
        lambda _agent_id: exec_state,
    )

    async def _list(agent_id: int, *, current_container_id=None):
        calls["list_agent_id"] = agent_id
        calls["current_container_id"] = current_container_id
        return containers

    monkeypatch.setattr(agentController.containerService, "list_agent_containers", _list)
    return calls


# ---------------------------------------------------------------------------
# 详情：exec_env / current_container_id / containers / container_count
# ---------------------------------------------------------------------------


async def test_detail_payload_reports_container_env_and_containers(monkeypatch):
    """在容器里时，详情页要如实报告环境、当前容器与容器表。"""
    container = _container_info()
    calls = _patch_detail_deps(
        monkeypatch,
        exec_state={"exec_env": ExecEnv.CONTAINER.value, "current_container_id": "c0ffee123456"},
        containers=[container],
    )

    payload = await agentController._build_agent_detail_payload(_agent())

    assert payload["exec_env"] == "container"
    assert payload["current_container_id"] == "c0ffee123456"
    assert payload["container_count"] == 1
    assert calls["current_container_id"] == "c0ffee123456"
    dumped = payload["containers"][0]
    # 前端拿 `record_id` 当行 key、`docker_id` 当容器 ID、`is_entered` 判断高亮
    assert dumped["record_id"] == 7
    assert dumped["docker_id"] == "c0ffee123456"
    assert dumped["is_entered"] is True
    # `created_at` 必须是 JSON 字符串（datetime 直接进 JSON 会序列化失败）
    assert isinstance(dumped["created_at"], str)


async def test_detail_payload_includes_pre_records_with_null_docker_id(monkeypatch):
    """预记录（`docker_id = null`）也必须出现在容器表里，并计入 `container_count`。

    预记录是「正在创建」/「创建失败待清理」的唯一入口（只能按 `record_id` 删），
    把它过滤掉用户就没法清理这些记录。
    """
    _patch_detail_deps(
        monkeypatch,
        exec_state={"exec_env": ExecEnv.HOST.value, "current_container_id": None},
        containers=[_container_info(record_id=8, docker_id=None)],
    )

    payload = await agentController._build_agent_detail_payload(_agent())

    assert payload["container_count"] == 1
    assert payload["containers"][0]["docker_id"] is None
    assert payload["containers"][0]["is_entered"] is False


async def test_detail_payload_defaults_to_host_without_containers(monkeypatch):
    """没有任何容器时是空列表而不是 None（前端直接 `.length` / 遍历）。"""
    _patch_detail_deps(
        monkeypatch,
        exec_state={"exec_env": ExecEnv.HOST.value, "current_container_id": None},
        containers=[],
    )

    payload = await agentController._build_agent_detail_payload(_agent())

    assert payload["exec_env"] == "host"
    assert payload["current_container_id"] is None
    assert payload["containers"] == []
    assert payload["container_count"] == 0


# ---------------------------------------------------------------------------
# 列表：三字段 + 未加载回落 host
# ---------------------------------------------------------------------------


def test_exec_fields_fall_back_to_host_for_unloaded_agent():
    """未加载的 Agent 不在 `exec_env_map` 里 ⇒ 一律 host、count 0。

    不能假设「没查到就是在容器」：环境只在内存，后端重启后全部回宿主。
    """
    fields = agentController._build_agent_exec_fields(99, {1: {"exec_env": "container", "current_container_id": "c0ffee123456"}}, {1: 2})

    assert fields == {"exec_env": "host", "current_container_id": None, "container_count": 0}


def test_exec_fields_use_loaded_state_and_db_count():
    """已加载的 Agent 用 Driver 内存态 + DB 计数。"""
    fields = agentController._build_agent_exec_fields(
        1,
        {1: {"exec_env": "container", "current_container_id": "c0ffee123456"}},
        {1: 3},
    )

    assert fields == {
        "exec_env": "container",
        "current_container_id": "c0ffee123456",
        "container_count": 3,
    }


async def test_list_handler_fills_exec_fields_and_counts_from_db(monkeypatch):
    """列表接口把三字段逐条补齐；容器数来自 DB 批量查询，不逐个查 docker。"""
    agent = _agent()
    monkeypatch.setattr(
        agentController.gtTeamManager,
        "get_team_by_id",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(
        agentController.gtAgentManager,
        "get_team_all_agents",
        AsyncMock(return_value=[agent]),
    )
    monkeypatch.setattr(agentController.agentService, "get_team_runtime_status_map", lambda _team_id: {})
    monkeypatch.setattr(
        agentController.agentService,
        "get_team_exec_env_map",
        lambda _team_id: {3: {"exec_env": "container", "current_container_id": "c0ffee123456"}},
    )

    counted: dict = {}

    async def _count(agent_ids: list[int]) -> dict[int, int]:
        counted["ids"] = agent_ids
        return {3: 2}

    monkeypatch.setattr(agentController.containerService, "count_containers_by_agent_ids", _count)

    captured: list[dict] = []
    handler = object.__new__(agentController.AgentListHandler)
    handler.request = SimpleNamespace(query_arguments={"team_id": [b"1"]})
    handler.return_json = captured.append

    await handler.get()

    item = captured[0]["agents"][0]
    assert item["exec_env"] == "container"
    assert item["current_container_id"] == "c0ffee123456"
    assert item["container_count"] == 2
    assert item["status"] == AgentStatus.IDLE.name
    assert counted["ids"] == [3]


async def test_list_handler_reports_host_for_agent_missing_from_exec_map(monkeypatch):
    """列表里未加载的 Agent 显示 host / null / 0，而不是省略字段。

    省略字段会让前端 `normalizeAgent` 拿 undefined 去推断，容易出现「有时有徽标、
    有时没有」的随机表现。
    """
    monkeypatch.setattr(
        agentController.gtTeamManager,
        "get_team_by_id",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(
        agentController.gtAgentManager,
        "get_team_all_agents",
        AsyncMock(return_value=[_agent()]),
    )
    monkeypatch.setattr(agentController.agentService, "get_team_runtime_status_map", lambda _team_id: {})
    monkeypatch.setattr(agentController.agentService, "get_team_exec_env_map", lambda _team_id: {})
    monkeypatch.setattr(
        agentController.containerService,
        "count_containers_by_agent_ids",
        AsyncMock(return_value={}),
    )

    captured: list[dict] = []
    handler = object.__new__(agentController.AgentListHandler)
    handler.request = SimpleNamespace(query_arguments={"team_id": [b"1"]})
    handler.return_json = captured.append

    await handler.get()

    item = captured[0]["agents"][0]
    assert item["exec_env"] == "host"
    assert item["current_container_id"] is None
    assert item["container_count"] == 0
