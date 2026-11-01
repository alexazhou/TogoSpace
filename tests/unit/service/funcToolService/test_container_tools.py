"""容器沙箱工具的行为测试（§6）。

断言重点：
1. **默认工具集不含容器工具**（`get_tools()` 不带），只有 TSP driver 拿得到——
   Native / Claude SDK 的行为必须零变化；
2. 每个工具都把 `TogoException` 转成带 `error_code` 的失败返回体（模型能据此自纠）；
3. 工具**不自己判断环境状态**：进入 / 退出委托 `Agent`（执行环境属于
   Agent），其余容器操作委托 `containerService`（避免状态语义两份实现）；
4. docstring 面向 Agent，不出现 `attach` / `exec` / `client` / `stdio` 这类底层实现词。
"""

from __future__ import annotations

import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants import ContainerState, ExecEnv
from exception import TogoException
from model.coreModel import ContainerInfo, ExecEnvState
from service import funcToolService
from service.funcToolService import tools as tools_module
from service.roomService import ToolCallContext


@pytest.fixture(autouse=True)
def _loaded():
    """工具注册表需要有内容，`get_tools` 才有意义。

    直接调 `load_func_tools` / `load_container_func_tools` 而不是 `startup()`：
    后者是带状态门的一次性服务启动，同一进程内第二次调用会抛
    `SERVICE_ALREADY_STARTED`（测试之间会互相干扰）。
    """
    funcToolService.load_func_tools()
    funcToolService.load_container_func_tools()
    yield


def _ctx(agent_id: int = 1) -> ToolCallContext:
    return ToolCallContext(agent_id=agent_id, team_id=1, tool_name="test")


@pytest.fixture
def container_agent(monkeypatch):
    """把 `_container_agent` 换成假 Agent，绕开真实的 agent 查找与驱动校验。"""
    agent = MagicMock()
    agent.gt_agent.id = 1
    agent.agent_workdir = "/tmp/agent-workdir"
    agent.get_exec_env_state.return_value = ExecEnvState(exec_env=ExecEnv.HOST)
    monkeypatch.setattr(tools_module, "_container_agent", lambda _ctx: agent)
    return agent


# ---------------------------------------------------------------------------
# 工具集边界：容器工具只对 TSP driver 暴露
# ---------------------------------------------------------------------------


def test_default_tools_exclude_container_tools():
    """`get_tools()` 默认**不含**容器工具（Native / Claude SDK 行为零变化）。"""
    names = {tool.function.name for tool in funcToolService.get_tools()}

    assert not (names & set(funcToolService.CONTAINER_TOOL_NAMES))


def test_include_container_tools_adds_exactly_the_five():
    """`include_container_tools=True` 精确增加 5 个容器工具，不多不少。"""
    base = {tool.function.name for tool in funcToolService.get_tools()}
    with_containers = {
        tool.function.name
        for tool in funcToolService.get_tools(include_container_tools=True)
    }

    assert with_containers - base == set(funcToolService.CONTAINER_TOOL_NAMES)
    assert len(funcToolService.CONTAINER_TOOL_NAMES) == 5


def test_tsp_driver_is_the_only_driver_requesting_container_tools():
    """只有 TSP driver 请求容器工具。

    如果哪天别的 driver 也传 `include_container_tools=True`，容器工具会出现在
    不经过 gtsp 的驱动上——点了「进入容器」其实什么也没切，比不提供更糟。
    """
    from service.agentService.driver import claudeSdkDriver, nativeDriver, tspDriver

    for module in (nativeDriver, claudeSdkDriver):
        src = inspect.getsource(module)
        assert "include_container_tools=True" not in src, f"{module.__name__} 不应暴露容器工具"

    assert "include_container_tools=True" in inspect.getsource(tspDriver)


# ---------------------------------------------------------------------------
# docstring 规范：面向 Agent，不出现底层实现词
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", [
    "create_container", "enter_container", "exit_to_host",
    "list_containers", "delete_container",
])
def test_tool_docstrings_avoid_implementation_jargon(tool_name):
    """docstring 里不许出现 `attach` / `exec` / `client` / `stdio`（§6.3）。

    这些是系统内部实现，模型看到会试图在参数里传相关东西；描述应只说
    「进入容器 / 退出容器 / 在容器内执行」这类行为。
    """
    func = getattr(tools_module, tool_name)
    doc = inspect.getdoc(func) or ""

    for word in ("attach", "exec", "client", "stdio", "--entrypoint", "docker cp"):
        assert word not in doc.lower(), f"{tool_name} 的 docstring 出现了底层实现词: {word}"


@pytest.mark.parametrize("tool_name", [
    "create_container", "enter_container", "exit_to_host",
    "list_containers", "delete_container",
])
def test_tool_docstrings_have_args_section_only_when_needed(tool_name):
    """有参数的工具有 `Args:` 段，无参数的没有（空 `Args:` 会污染 schema 解析）。"""
    func = getattr(tools_module, tool_name)
    sig = inspect.signature(func)
    public_params = [p for p in sig.parameters if not p.startswith("_")]
    doc = inspect.getdoc(func) or ""

    if public_params:
        assert "Args:" in doc, f"{tool_name} 有参数但没有 Args 段"
    else:
        assert "Args:" not in doc, f"{tool_name} 无参数但写了 Args 段"


# ---------------------------------------------------------------------------
# create_container
# ---------------------------------------------------------------------------


async def test_create_container_returns_contract_fields(monkeypatch, container_agent):
    """返回字段与 §6.2 第 1 条一致：`state` 恒为 created，且明确「仍在宿主」。"""
    from service import containerService

    record = MagicMock()
    record.container_id = "c0ffee123456"
    record.container_name = "agent-1-1"
    record.image = "ubuntu:24.04"
    monkeypatch.setattr(
        containerService, "create_container", AsyncMock(return_value=record)
    )

    result = await tools_module.create_container(_context=_ctx())

    assert result["success"] is True
    assert result["container_id"] == "c0ffee123456"
    assert result["state"] == "created"
    assert set(result["mounts"]) == {"/workspace/self", "/workspace/team_shared"}
    # 必须提醒模型「还没进去」，否则它会以为创建完就已经在容器里了
    assert "enter_container" in result["message"]


async def test_create_container_passes_image_through(monkeypatch, container_agent):
    """`image` 原样透传（空值交给 service 走默认，不在工具层重复判断）。"""
    from service import containerService

    record = MagicMock(container_id="c0ffee123456", container_name="n", image="python:3.11")
    mocked = AsyncMock(return_value=record)
    monkeypatch.setattr(containerService, "create_container", mocked)

    await tools_module.create_container(image="python:3.11", _context=_ctx())

    assert mocked.await_args.kwargs["image"] == "python:3.11"


async def test_create_container_maps_togo_exception_to_error_result(monkeypatch, container_agent):
    """`TogoException` ⇒ `success=False` + `error_code`（模型据此自纠）。"""
    from service import containerService

    monkeypatch.setattr(
        containerService, "create_container",
        AsyncMock(side_effect=TogoException("已达上限", "container_limit_reached")),
    )

    result = await tools_module.create_container(_context=_ctx())

    assert result["success"] is False
    assert result["error_code"] == "container_limit_reached"
    assert result["message"] == "已达上限"


# ---------------------------------------------------------------------------
# enter_container / exit_to_host
# ---------------------------------------------------------------------------


async def test_enter_container_reports_mount_info_for_managed(monkeypatch, container_agent):
    """自建容器：返回挂载说明与「退出后自动停止」的提示。"""
    from model.coreModel import ContainerTarget

    target = ContainerTarget(
        container_id="c0ffee123456", is_managed=True, arch="arm64", container_name="agent-1-1"
    )
    container_agent.enter_container = AsyncMock(
        return_value=(target, ContainerState.CREATED)
    )

    result = await tools_module.enter_container("c0ffee123456", _context=_ctx())

    assert result["success"] is True
    assert result["exec_env"] == "container"
    assert result["container_id"] == "c0ffee123456"
    # §6.2 要求返回真实容器名，不能给 None
    assert result["container_name"] == "agent-1-1"
    assert result["mounted"] is True
    assert result["workdir"] == "/workspace/self"
    assert "自动停止" in result["message"]


async def test_enter_container_reports_external_container_difference(monkeypatch, container_agent):
    """外部容器：`mounted=False`、不报 workdir，并说明原进程不受影响。"""
    from model.coreModel import ContainerTarget

    target = ContainerTarget(container_id="deadbeef0002", is_managed=False, arch="arm64")
    container_agent.enter_container = AsyncMock(
        return_value=(target, ContainerState.RUNNING)
    )

    result = await tools_module.enter_container("deadbeef0002", _context=_ctx())

    assert result["mounted"] is False
    assert result["workdir"] == ""
    # 外部容器名取不到 inspect 的 `Name` 时必须是 null，不能是空串
    # （空串在 JSON 里看起来像一个合法的容器名）
    assert result["container_name"] is None
    assert "不是由你创建" in result["message"]


async def test_enter_container_maps_state_errors(monkeypatch, container_agent):
    """状态错误码要透传（`container_already_running` 等），不能统一成「进入失败」。"""
    container_agent.enter_container = AsyncMock(
        side_effect=TogoException("running 残留", "container_already_running")
    )

    result = await tools_module.enter_container("c0ffee123456", _context=_ctx())

    assert result["error_code"] == "container_already_running"


async def test_exit_to_host_reports_host_workdir(monkeypatch, container_agent):
    """退出后必须告诉模型宿主工作目录在哪、容器路径已失效。"""
    container_agent.exit_to_host = AsyncMock(return_value=None)

    result = await tools_module.exit_to_host(_context=_ctx())

    assert result["success"] is True
    assert result["exec_env"] == "host"
    assert result["workdir"] == "/tmp/agent-workdir"
    assert "/workspace/self" in result["message"]


async def test_exit_to_host_propagates_failure(monkeypatch, container_agent):
    """退出失败（宿主连不上）必须报错——不能返回成功让 Agent 以为已在宿主。"""
    container_agent.exit_to_host = AsyncMock(
        side_effect=TogoException("宿主连接失败", "container_enter_failed")
    )

    result = await tools_module.exit_to_host(_context=_ctx())

    assert result["success"] is False
    assert result["error_code"] == "container_enter_failed"


# ---------------------------------------------------------------------------
# list_containers
# ---------------------------------------------------------------------------


async def test_list_containers_returns_env_and_serialized_containers(monkeypatch, container_agent):
    """返回 `exec_env` / `current_container_id` / `containers`，且容器已 JSON 化。"""
    from service import containerService

    info = ContainerInfo(
        record_id=1, agent_id=1, container_id="c0ffee123456",
        container_name="agent-1-1", image="ubuntu:24.04",
        created_at=datetime(2026, 11, 1, 10, 0, 0),
        state=ContainerState.EXITED, is_entered=True,
    )
    container_agent.get_exec_env_state.return_value = ExecEnvState(
        exec_env=ExecEnv.CONTAINER,
        current_container_id="c0ffee123456",
    )
    monkeypatch.setattr(containerService, "is_docker_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        containerService, "list_agent_containers", AsyncMock(return_value=[info])
    )

    result = await tools_module.list_containers(_context=_ctx())

    assert result["exec_env"] == "container"
    assert result["current_container_id"] == "c0ffee123456"
    assert result["docker_available"] is True
    # 必须是可 JSON 序列化的 dict（created_at 已转字符串）
    assert isinstance(result["containers"][0], dict)
    # §6.2 契约使用 `container_id`，且不能带内部字段 agent_id
    assert result["containers"][0]["container_id"] == "c0ffee123456"
    assert "agent_id" not in result["containers"][0]
    assert isinstance(result["containers"][0]["created_at"], str)


async def test_list_containers_keeps_records_when_docker_unavailable(monkeypatch, container_agent):
    """Docker 不可用时仍返回记录（`docker_available=False`），不给空列表。

    给空列表会让模型以为「容器都没了」，进而重复创建。
    """
    from service import containerService

    info = ContainerInfo(
        record_id=1, agent_id=1, container_id=None,
        container_name="agent-1-1", image="ubuntu:24.04", created_at=None,
        state=ContainerState.UNKNOWN,
    )
    monkeypatch.setattr(containerService, "is_docker_available", AsyncMock(return_value=False))
    monkeypatch.setattr(
        containerService, "list_agent_containers", AsyncMock(return_value=[info])
    )

    result = await tools_module.list_containers(_context=_ctx())

    assert result["docker_available"] is False
    assert len(result["containers"]) == 1
    assert result["containers"][0]["state"] == "unknown"


# ---------------------------------------------------------------------------
# delete_container
# ---------------------------------------------------------------------------


async def test_delete_container_returns_deleted_ids(monkeypatch, container_agent):
    """返回 `deleted_record_id` 与 `deleted_container_id`（预记录后者为 null）。"""
    from service import containerService

    record = MagicMock(id=7, container_id="c0ffee123456")
    monkeypatch.setattr(
        containerService, "delete_container", AsyncMock(return_value=record)
    )

    result = await tools_module.delete_container(container_id="c0ffee123456", _context=_ctx())

    assert result["deleted_record_id"] == 7
    assert result["deleted_container_id"] == "c0ffee123456"
    assert "文件不受影响" in result["message"]


async def test_delete_container_reports_null_container_id_for_pre_record(monkeypatch, container_agent):
    """删除预记录时 `deleted_container_id` 为 null（契约明确）。"""
    from service import containerService

    record = MagicMock(id=7, container_id=None)
    monkeypatch.setattr(
        containerService, "delete_container", AsyncMock(return_value=record)
    )

    result = await tools_module.delete_container(record_id=7, _context=_ctx())

    assert result["deleted_container_id"] is None


async def test_delete_container_passes_current_container_id(monkeypatch, container_agent):
    """必须把当前所处容器传给 service，否则「正在使用」的校验就失效了。"""
    from service import containerService

    container_agent.get_exec_env_state.return_value = ExecEnvState(
        exec_env=ExecEnv.CONTAINER,
        current_container_id="c0ffee123456",
    )
    mocked = AsyncMock(return_value=MagicMock(id=7, container_id="c0ffee123456"))
    monkeypatch.setattr(containerService, "delete_container", mocked)

    await tools_module.delete_container(container_id="c0ffee123456", _context=_ctx())

    assert mocked.await_args.kwargs["current_container_id"] == "c0ffee123456"


async def test_delete_container_maps_in_use_error(monkeypatch, container_agent):
    """`container_in_use` 要透传，模型才知道该先 `exit_to_host`。"""
    from service import containerService

    monkeypatch.setattr(
        containerService, "delete_container",
        AsyncMock(side_effect=TogoException("请先退出", "container_in_use")),
    )

    result = await tools_module.delete_container(container_id="c0ffee123456", _context=_ctx())

    assert result["error_code"] == "container_in_use"


# ---------------------------------------------------------------------------
# 非 TSP driver / 无上下文
# ---------------------------------------------------------------------------


async def test_container_tools_reject_non_tsp_driver(monkeypatch):
    """非 TSP driver 调用容器工具 ⇒ 明确报错，而不是静默失败或 AttributeError。"""
    from service import agentService

    agent = MagicMock()
    agent.require_container_driver.side_effect = TogoException(
        "当前 Agent 的执行驱动不支持容器沙箱（仅支持 TSP 驱动）",
        "container_invalid_params",
    )
    monkeypatch.setattr(agentService, "get_agent_or_none", lambda _id: agent)

    result = await tools_module.list_containers(_context=_ctx())

    assert result["success"] is False
    assert result["error_code"] == "container_invalid_params"
    assert "TSP" in result["message"]


async def test_container_tools_reject_missing_context():
    """没有 Agent 上下文时不许调用（否则无法确定切谁的环境）。"""
    result = await tools_module.exit_to_host(_context=None)

    assert result["success"] is False
    assert result["error_code"] == "container_invalid_params"
