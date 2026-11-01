"""`TspAgentDriver` 的执行环境切换测试（第 4 步）。

只测**环境状态机**，不真连 docker 也不真连 gtsp：`TSPClient.from_stdio` 被打桩
成一个可控的假 client，因此这里断言的是「什么时候切环境、失败时环境保持什么、
命令是怎么构造的」。

为什么这些断言重要（不是形式主义）：
- 环境状态只由 `connect()` 成功时更新 → 否则退出失败会把 Agent 静默丢回宿主；
- `_ensure_connected` 只重连当前环境 → 否则容器里写的文件会突然写到宿主；
- `request_timeout_sec` 不能传 `enter_timeout_sec` → 否则容器内所有工具请求
  的响应上限被压到 60s。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants import ExecEnv
from model.coreModel import ContainerTarget
from model.dbModel.gtAgent import GtAgent
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver import tspDriver
from service.agentService.driver.tspDriver import TspAgentDriver
from service.agentService.toolRegistry import AgentToolRegistry

_MANAGED = ContainerTarget(container_id="c0ffee123456", is_managed=True, arch="arm64")
_EXTERNAL = ContainerTarget(container_id="deadbeef0002", is_managed=False, arch="amd64")


@dataclass
class _DummyHost:
    gt_agent: GtAgent = field(
        default_factory=lambda: GtAgent(id=1, team_id=1, name="实习生", role_template_id=1)
    )
    system_prompt: str = ""
    agent_workdir: str = "/tmp/agent-workdir"
    _history: AgentHistoryStore = field(default_factory=lambda: AgentHistoryStore(agent_id=1))
    tool_registry: AgentToolRegistry = field(default_factory=AgentToolRegistry)

    async def execute_pending_tools(self) -> None:  # pragma: no cover - 协议要求
        return None


class _FakeTSPClient:
    """可配置成败的假 TSP client。"""

    instances: list["_FakeTSPClient"] = []

    def __init__(self, *, fail_initialize: bool = False, tools: list | None = None) -> None:
        self.fail_initialize = fail_initialize
        self.tools = tools or []
        self.disconnected = False
        self.process = MagicMock(returncode=None)
        type(self).instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    async def initialize(self, **_kwargs) -> None:
        if self.fail_initialize:
            raise RuntimeError("initialize failed")

    async def disconnect(self) -> None:
        self.disconnected = True


def _patch_client(monkeypatch, *, fail_initialize: bool = False) -> dict:
    """打桩 `TSPClient.from_stdio`，返回记录「用了什么命令与超时」的容器。"""
    _FakeTSPClient.reset()
    captured: dict = {"commands": [], "timeouts": []}

    class _Factory:
        def __init__(self, command, timeout_sec):
            captured["commands"].append(list(command))
            captured["timeouts"].append(timeout_sec)
            self._client = _FakeTSPClient(fail_initialize=fail_initialize)

        async def start(self):
            return self._client

    monkeypatch.setattr(tspDriver.TSPClient, "from_stdio", _Factory)
    return captured


def _build_driver(**options) -> TspAgentDriver:
    base = {
        "request_timeout_sec": 190,
        "workdir": "/tmp/agent-workdir",
        "command": ["/fake/gtsp", "--mode", "stdio"],
    }
    base.update(options)
    driver = TspAgentDriver(_DummyHost(), AgentDriverConfig(driver_type="tsp", options=base))
    return driver


# ---------------------------------------------------------------------------
# 初始状态
# ---------------------------------------------------------------------------


def test_driver_starts_in_host_env_without_container():
    """驱动程序启动时不恢复容器环境：一律回宿主（§4.1「重启回宿主」）。

    这是「不落库」的直接后果——外部容器无法可靠恢复，若 DB 说在容器而实际不在，
    Agent 的第一次工具调用就会打到错误的环境。
    """
    driver = _build_driver()

    assert driver.exec_env == ExecEnv.HOST
    assert driver.current_container is None
    assert driver.current_container_id is None


# ---------------------------------------------------------------------------
# connect：宿主 / 自建容器 / 外部容器 三种命令
# ---------------------------------------------------------------------------


async def test_connect_host_uses_host_command(monkeypatch):
    """连宿主用 `build_gtsp_command` 的结果（不经 docker）。"""
    captured = _patch_client(monkeypatch)
    driver = _build_driver()

    assert await driver.connect(ExecEnv.HOST) is True

    # `build_gtsp_command` 会补 `--workdir` 与 `--log-path`，所以只断言前缀与
    # 「不经 docker」这一点
    command = captured["commands"][0]
    assert command[:3] == ["/fake/gtsp", "--mode", "stdio"]
    assert "docker" not in command[0]
    assert driver.exec_env == ExecEnv.HOST
    assert driver.current_container is None


async def test_connect_managed_container_uses_start_attach(monkeypatch):
    """自建容器必须用 `docker start -a -i <id>`（形态 A）。

    用 exec 会因容器未 running 失败；用 start 让容器主进程（gtsp）接管 stdio，
    关 stdin 即自动停回 exited。
    """
    captured = _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, _MANAGED) is True

    command = captured["commands"][0]
    assert command[0] == "/fake/docker"
    assert command[1:] == ["start", "-a", "-i", "c0ffee123456"]
    assert driver.exec_env == ExecEnv.CONTAINER
    assert driver.current_container == _MANAGED
    assert driver.current_container_id == "c0ffee123456"


async def test_connect_external_container_uses_exec(monkeypatch):
    """外部容器必须用 `docker exec -i <id> /tmp/gtsp ...`（形态 B）。

    外部容器的主进程不能被替换，只能额外 exec 一个 gtsp。
    """
    captured = _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, _EXTERNAL) is True

    command = captured["commands"][0]
    assert command[0] == "/fake/docker"
    assert command[1:3] == ["exec", "-i"]
    assert command[3] == "deadbeef0002"
    assert command[4] == "/tmp/gtsp"  # CONTAINER_GTSP_PATH（常量在 constants 里）
    assert "start" not in command
    assert "-a" not in command


async def test_connect_container_without_target_returns_false(monkeypatch):
    """`connect(CONTAINER)` 缺 container 参数 ⇒ 直接 False，不做任何连接。"""
    captured = _patch_client(monkeypatch)
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, None) is False

    assert captured["commands"] == []
    assert driver.exec_env == ExecEnv.HOST


# ---------------------------------------------------------------------------
# 超时参数：request_timeout 不能被 enter_timeout 顶掉
# ---------------------------------------------------------------------------


async def test_connect_uses_request_timeout_not_enter_timeout(monkeypatch):
    """`TSPClient` 的超时必须是 `request_timeout_sec`（190s），不是 60s。

    若传了 60s，容器内所有工具请求的响应上限都会被压到 60s——在容器里跑一次
    稍慢的编译就会假超时。这条是硬约束（§4.3）。
    """
    captured = _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver(request_timeout_sec=190)

    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    assert captured["timeouts"] == [190]
    assert captured["timeouts"] != [tspDriver.CONTAINER_ENTER_TIMEOUT_SEC]


async def test_connect_times_out_after_enter_timeout(monkeypatch):
    """「拉起 + 握手」整段受 `CONTAINER_ENTER_TIMEOUT_SEC` 约束，超时返回 False。"""
    _FakeTSPClient.reset()

    class _SlowFactory:
        def __init__(self, command, timeout_sec):
            pass

        async def start(self):
            await asyncio.sleep(10)
            return _FakeTSPClient()

    monkeypatch.setattr(tspDriver.TSPClient, "from_stdio", _SlowFactory)
    monkeypatch.setattr(tspDriver, "CONTAINER_ENTER_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, _MANAGED) is False
    # 超时后环境不变
    assert driver.exec_env == ExecEnv.HOST


# ---------------------------------------------------------------------------
# 失败时环境状态必须保持原值（这是整个设计的关键不变量）
# ---------------------------------------------------------------------------


async def test_failed_connect_keeps_previous_env(monkeypatch):
    """`connect(CONTAINER)` 握手失败时环境**保持 HOST**（不能变成「说在容器」）。"""
    _patch_client(monkeypatch, fail_initialize=True)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, _MANAGED) is False

    assert driver.exec_env == ExecEnv.HOST
    assert driver.current_container is None


async def test_failed_exit_connect_keeps_container_env(monkeypatch):
    """`exit_to_host` 里 connect(HOST) 失败 ⇒ 环境仍是 CONTAINER（§5.2 第 3 步）。

    这条是本次重构的核心原因：若失败后环境变成 HOST，Agent 下一次工具调用会
    连宿主，静默改变执行环境；保持 CONTAINER 才能让它重试 exit_to_host，
    而 `_ensure_connected` 会重连容器。
    """
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)
    assert driver.exec_env == ExecEnv.CONTAINER

    # 让之后的连接全部失败
    _patch_client(monkeypatch, fail_initialize=True)
    await driver.disconnect()
    assert await driver.connect(ExecEnv.HOST) is False

    assert driver.exec_env == ExecEnv.CONTAINER
    assert driver.current_container == _MANAGED


async def test_failed_connect_closes_the_partially_created_client(monkeypatch):
    """握手失败必须关掉已拉起的进程（否则容器内 gtsp 一直挂着）。"""
    _patch_client(monkeypatch, fail_initialize=True)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()

    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    assert _FakeTSPClient.instances
    assert all(client.disconnected for client in _FakeTSPClient.instances)


# ---------------------------------------------------------------------------
# disconnect：只清连接，不动环境
# ---------------------------------------------------------------------------


async def test_disconnect_does_not_reset_exec_env(monkeypatch):
    """`disconnect()` 不重置 `_exec_env` / `_current_container`（§4.3）。

    必须保持容器环境，才让「disconnect → connect(HOST)」这个退出流程在
    connect 失败时不丢状态。
    """
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    await driver.disconnect()

    assert driver.exec_env == ExecEnv.CONTAINER
    assert driver.current_container == _MANAGED
    assert driver._client is None
    assert driver._tsp_tools == {}


async def test_disconnect_closes_client(monkeypatch):
    """`disconnect()` 真的关闭了 client（关 stdin 是容器停回 exited 的唯一触发）。"""
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)
    client = driver._client

    await driver.disconnect()

    assert client is not None and client.disconnected is True


# ---------------------------------------------------------------------------
# _ensure_connected：只重连当前环境，绝不跨环境回落
# ---------------------------------------------------------------------------


async def test_ensure_connected_reconnects_current_container_not_host(monkeypatch):
    """容器环境断开后重连**仍是容器**，绝不悄悄回落宿主（§4.3）。

    回落宿主会让容器里写的文件突然写到宿主目录——比直接失败危险得多。
    """
    captured = _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    # 模拟连接断开
    driver._client = None
    captured["commands"].clear()

    assert await driver._ensure_connected() is True

    assert captured["commands"][0][1:] == ["start", "-a", "-i", "c0ffee123456"]
    assert driver.exec_env == ExecEnv.CONTAINER


async def test_ensure_connected_reconnects_host_when_in_host(monkeypatch):
    """宿主环境断开后重连宿主（不受容器影响）。"""
    captured = _patch_client(monkeypatch)
    driver = _build_driver()
    await driver.connect(ExecEnv.HOST)
    driver._client = None
    captured["commands"].clear()

    assert await driver._ensure_connected() is True

    assert captured["commands"][0][:3] == ["/fake/gtsp", "--mode", "stdio"]
    assert driver.exec_env == ExecEnv.HOST


async def test_ensure_connected_returns_true_when_already_connected(monkeypatch):
    """已连接时直接返回 True，不重复拉起进程。"""
    captured = _patch_client(monkeypatch)
    driver = _build_driver()
    await driver.connect(ExecEnv.HOST)
    captured["commands"].clear()

    assert await driver._ensure_connected() is True

    assert captured["commands"] == []


async def test_ensure_connected_does_not_deadlock_when_env_is_container(monkeypatch):
    """回归：`_ensure_connected` 在持锁状态下不能再调 `connect()`（不可重入锁会死锁）。

    这里用一个短超时保护：若实现回退成「锁内调 connect()」，本用例会因超时而
    失败，而不是把整个测试套件挂住。
    """
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)
    driver._client = None

    result = await asyncio.wait_for(driver._ensure_connected(), timeout=5)

    assert result is True


# ---------------------------------------------------------------------------
# 切换：A → B 只保留一个 client
# ---------------------------------------------------------------------------


async def test_switching_containers_closes_previous_client(monkeypatch):
    """A → B 切换时旧连接必须关闭（同一时刻只连一个环境，§1.3）。"""
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)
    first = driver._client

    other = ContainerTarget(container_id="deadbeef0002", is_managed=True, arch="arm64")
    await driver.connect(ExecEnv.CONTAINER, other)

    assert first is not None and first.disconnected is True
    assert driver.current_container == other
    assert len(_FakeTSPClient.instances) == 2


# ---------------------------------------------------------------------------
# AGENT_CONTAINER_STATE_CHANGED：只在环境真的变了的时候广播（§8）
# ---------------------------------------------------------------------------


def _capture_publishes(monkeypatch) -> list[dict]:
    """截获 messageBus.publish，返回已发布的 (topic, payload) 列表。"""
    published: list[dict] = []

    def _publish(topic, **payload):
        published.append({"topic": topic, **payload})

    # 打在 `service.messageBus.publish` 上（driver 里也是 `messageBus.publish`），
    # 不替换整个 messageBus 模块，避免把订阅机制一起抹掉。
    monkeypatch.setattr(tspDriver.messageBus, "publish", _publish)
    return published


async def test_publishes_container_state_on_enter(monkeypatch):
    """进入容器时广播一次，payload 带完整 gt_agent / exec_env / current_container_id。

    `gt_agent` 必须是完整对象：前端用 `readGtAgentIdentity` 做身份校验，缺字段
    这条事件会被整条丢弃。
    """
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    published = _capture_publishes(monkeypatch)
    driver = _build_driver()

    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    assert len(published) == 1
    event = published[0]
    assert event["topic"] == tspDriver.MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED
    assert event["exec_env"] == "container"
    assert event["current_container_id"] == "c0ffee123456"
    assert event["gt_agent"] is driver.host.gt_agent


async def test_publishes_host_state_on_exit(monkeypatch):
    """退出回宿主时也要广播，否则前端会一直显示「在容器里」。"""
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    published = _capture_publishes(monkeypatch)
    await driver.disconnect()
    await driver.connect(ExecEnv.HOST)

    assert len(published) == 1
    assert published[0]["exec_env"] == "host"
    assert published[0]["current_container_id"] is None


async def test_does_not_publish_when_env_unchanged(monkeypatch):
    """重连同一个环境**不广播**：否则容器断线自动重连会反复刷「进入容器」。

    这条同时守住「只在变化时发」这个语义——事件是给 UI 的增量，不是心跳。
    """
    _patch_client(monkeypatch)
    driver = _build_driver()
    await driver.connect(ExecEnv.HOST)

    published = _capture_publishes(monkeypatch)
    # 模拟连接断开后自动重连同一个环境
    driver._client = None
    await driver._ensure_connected()

    assert published == []


async def test_publishes_when_switching_between_containers(monkeypatch):
    """A 容器 → B 容器：`exec_env` 没变但容器变了，必须广播。"""
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    driver = _build_driver()
    await driver.connect(ExecEnv.CONTAINER, _MANAGED)

    published = _capture_publishes(monkeypatch)
    other = ContainerTarget(container_id="deadbeef0002", is_managed=True, arch="arm64")
    await driver.connect(ExecEnv.CONTAINER, other)

    assert len(published) == 1
    assert published[0]["current_container_id"] == "deadbeef0002"


async def test_publish_failure_does_not_break_connect(monkeypatch):
    """广播失败不能影响连接结果：状态通知的订阅者不该决定环境能否切换。"""
    _patch_client(monkeypatch)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))

    def _boom(*_args, **_kwargs):
        raise RuntimeError("no running loop")

    monkeypatch.setattr(tspDriver.messageBus, "publish", _boom)
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, _MANAGED) is True
    assert driver.exec_env == ExecEnv.CONTAINER


async def test_no_publish_on_failed_connect(monkeypatch):
    """连接失败时不广播：环境没变，广播会让 UI 提前显示「已进入」。"""
    _patch_client(monkeypatch, fail_initialize=True)
    monkeypatch.setattr(tspDriver.container_cli, "get_cli", lambda: MagicMock(binary_path="/fake/docker"))
    published = _capture_publishes(monkeypatch)
    driver = _build_driver()

    assert await driver.connect(ExecEnv.CONTAINER, _MANAGED) is False

    assert published == []
