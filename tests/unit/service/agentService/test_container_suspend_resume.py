"""`exit_to_host(keep_alive=True)` 挂起与 `enter_container` 复用（§3.2 / §3.3 / §3.4）。

这几条用例钉的是**「保活到底省掉了什么」**：
- `keep_alive=True` 时不许调 `disconnect()`（一断 stdin，容器内 gtsp 就退出，
  自建容器停回 exited，保活就名存实亡）；
- 挂起池命中时必须**一步都不走**正常进入流程（`validate_enter_state` / 注入 /
  `connect`），否则复用没有意义；
- 挂起会话已死时必须清掉死对象并回退正常流程，不能把死连接当活连接用。

Driver 继承真实的 `TspAgentDriver`：挂起池、锁、复用判定全部跑真实代码，只把
`_connect_target`（真正拉进程/握手的地方）换成造 fake client 的假实现。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from constants import ContainerArch, ContainerState, DriverType, ExecEnv
from exception import TogoException
from model.coreModel import ContainerTarget
from model.dbModel.gtAgent import GtAgent
from service import containerService
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver.tspDriver import TspAgentDriver

_MANAGED = ContainerTarget(container_id="c0ffee123456", is_managed=True, arch=ContainerArch.ARM64)
_MANAGED_B = ContainerTarget(container_id="beef12345678", is_managed=True, arch=ContainerArch.ARM64)
_EXTERNAL = ContainerTarget(container_id="deadbeef0002", is_managed=False, arch=ContainerArch.ARM64)


class _FakeClient:
    """最小 TSPClient 替身：只提供 Driver 真正用到的 `process` / `tools` / `disconnect()`。"""

    def __init__(self, *, alive: bool = True) -> None:
        self.tools: list[object] = []
        self.disconnect_calls = 0
        self.process = SimpleNamespace(returncode=None if alive else 1)

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        # 真实 disconnect 会终止子进程：这里同步反映到 returncode 上，
        # 免得「关掉的 client 仍被判活」这类错误状态混进后续断言。
        if self.process.returncode is None:
            self.process.returncode = 0


class _Driver(TspAgentDriver):
    """真实挂起池逻辑 + 假的连接原语。"""

    def __init__(self, *, connect_ok: dict[ExecEnv, bool] | None = None) -> None:
        super().__init__(
            SimpleNamespace(
                gt_agent=GtAgent(id=1, team_id=1, name="小美", role_template_id=1),
                agent_workdir="/tmp/agent-workdir",
            ),
            AgentDriverConfig(
                driver_type=DriverType.TSP,
                options={"command": ["/fake/gtsp", "--mode", "stdio"]},
            ),
        )
        self.connect_calls: list[tuple[ExecEnv, str | None]] = []
        self.published: list[str | None] = []
        self._connect_ok = dict(connect_ok or {})

    # 只替换「真的拉进程 / 载工具 / 广播」这三件与外部世界交互的事
    async def _connect_target(self, env: ExecEnv, container: ContainerTarget | None) -> bool:
        self.connect_calls.append((env, container.container_id if container else None))
        if not self._connect_ok.get(env, True):
            return False
        client = _FakeClient()
        await self._close_client_quietly(self._client)
        self._client = client
        self._current_container = container
        return True

    def _register_host_tools(self) -> None:
        return None

    def _publish_container_state(self) -> None:
        self.published.append(self.current_container_id)

    def _seed_active_client(self, container: ContainerTarget | None, *, alive: bool = True) -> _FakeClient:
        """把 Driver 摆成「已连在某个环境上」的初始态。"""
        client = _FakeClient(alive=alive)
        self._client = client
        self._current_container = container
        return client


@pytest.fixture(autouse=True)
def _patched_container_primitives(monkeypatch):
    """容器原语一律打桩：这里测的是编排，不测 docker。"""
    state = {"target": _MANAGED, "state": ContainerState.EXITED, "prepare_calls": [], "resolve_error": None}

    async def _resolve(agent_id, container_id):
        if state["resolve_error"] is not None:
            raise state["resolve_error"]
        return state["target"], state["state"]

    async def _prepare(target):
        state["prepare_calls"].append(target)

    monkeypatch.setattr(containerService, "resolve_enter_target", _resolve)
    monkeypatch.setattr(containerService, "prepare_container_gtsp", _prepare)
    return state


# ---------------------------------------------------------------------------
# exit_to_host(keep_alive=...)
# ---------------------------------------------------------------------------


async def test_exit_to_host_default_disconnects_and_does_not_suspend():
    """默认退出必须真的断开：挂起池为空，client 的进程被终止。"""
    driver = _Driver()
    client = driver._seed_active_client(_MANAGED)

    assert await driver.exit_to_host() is True

    assert driver._suspended_clients == {}
    assert client.disconnect_calls == 1
    assert driver.exec_env == ExecEnv.HOST


async def test_exit_to_host_keep_alive_suspends_without_disconnecting():
    """保活退出：client 进挂起池且**一次 disconnect 都不许调**。

    一旦断开，容器内 gtsp 收到 EOF 退出，自建容器停回 exited —— 保活就没了。
    """
    driver = _Driver()
    client = driver._seed_active_client(_MANAGED)

    assert await driver.exit_to_host(keep_alive=True) is True

    assert driver._suspended_clients == {"c0ffee123456": client}
    assert client.disconnect_calls == 0
    assert client.process.returncode is None
    # 切宿主后活跃连接是新的宿主 client，**不是**被挂起的那一个（否则等于两处共用）
    assert driver._client is not client
    assert driver.exec_env == ExecEnv.HOST


async def test_exit_to_host_keep_alive_replaces_stale_pool_entry():
    """同一容器重复挂起：旧对象被关掉，池里只留最新的那个 client。"""
    driver = _Driver()
    old = _FakeClient()
    driver._suspended_clients["c0ffee123456"] = old
    current = driver._seed_active_client(_MANAGED)

    await driver.exit_to_host(keep_alive=True)

    assert driver._suspended_clients == {"c0ffee123456": current}
    assert old.disconnect_calls == 1


async def test_exit_to_host_default_clears_stale_suspended_entry():
    """非保活退出必须顺手清掉同容器的历史挂起对象，否则那个子进程没人回收。"""
    driver = _Driver()
    stale = _FakeClient()
    driver._suspended_clients["c0ffee123456"] = stale
    driver._seed_active_client(_MANAGED)

    await driver.exit_to_host(keep_alive=False)

    assert driver._suspended_clients == {}
    assert stale.disconnect_calls == 1


async def test_exit_to_host_keep_alive_skips_suspending_dead_client():
    """当前 client 进程已死 ⇒ 不进池（复用时会立刻判死，等于占位）。"""
    driver = _Driver()
    dead = driver._seed_active_client(_MANAGED, alive=False)

    await driver.exit_to_host(keep_alive=True)

    assert driver._suspended_clients == {}
    assert dead.disconnect_calls == 1


async def test_exit_to_host_keep_alive_host_connect_failure_keeps_suspended_for_retry():
    """切宿主失败：环境仍是容器、挂起 client 不丢，重试能走通。

    这是「不许假装成功」的核心用例：失败后 `_current_container` 若被清空，
    Agent 会以为已在宿主，后续命令全打在错误环境上。
    """
    driver = _Driver(connect_ok={ExecEnv.HOST: False})
    client = driver._seed_active_client(_MANAGED)

    with pytest.raises(TogoException) as excinfo:
        await driver.exit_to_host(keep_alive=True)

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ENTER_FAILED
    assert driver.current_container_id == "c0ffee123456"
    assert driver._suspended_clients == {"c0ffee123456": client}

    # 重试：宿主可连了 ⇒ 成功，且挂起对象仍在池里（没被上一次失败覆盖成 None）
    driver._connect_ok[ExecEnv.HOST] = True
    assert await driver.exit_to_host(keep_alive=True) is True
    assert driver._suspended_clients == {"c0ffee123456": client}
    assert driver.exec_env == ExecEnv.HOST


# ---------------------------------------------------------------------------
# enter_container 复用
# ---------------------------------------------------------------------------


async def test_enter_container_reuses_suspended_without_any_connect(_patched_container_primitives):
    """命中挂起池：不校验状态、不注入、不 connect，一步都不走。"""
    driver = _Driver()
    suspended = _FakeClient()
    driver._suspended_clients["c0ffee123456"] = suspended
    # 保活中容器是 running，正常流程会判「异常残留」拦下——复用必须在它之前短路
    _patched_container_primitives["target"] = _MANAGED
    _patched_container_primitives["state"] = ContainerState.RUNNING

    target, state = await driver.enter_container("c0ffee123456")

    assert (target, state) == (_MANAGED, ContainerState.RUNNING)
    assert driver.connect_calls == []
    assert _patched_container_primitives["prepare_calls"] == []
    assert driver._client is suspended
    assert driver._suspended_clients == {}
    assert driver.current_container_id == "c0ffee123456"
    assert driver.published == ["c0ffee123456"]


async def test_enter_container_reuses_multiple_suspended_containers(_patched_container_primitives):
    """多容器并存：先后挂起 A、B，再分别切回，各自命中自己的 client。"""
    driver = _Driver()
    client_a = _FakeClient()
    client_b = _FakeClient()
    driver._suspended_clients["c0ffee123456"] = client_a
    driver._suspended_clients["beef12345678"] = client_b

    _patched_container_primitives["target"] = _MANAGED
    _patched_container_primitives["state"] = ContainerState.RUNNING
    await driver.enter_container("c0ffee123456")
    assert driver._client is client_a

    _patched_container_primitives["target"] = _MANAGED_B
    await driver.enter_container("beef12345678")
    assert driver._client is client_b
    # 跨容器切换：A 的 client 已被关闭，不是留着不关
    assert client_a.disconnect_calls == 1
    assert driver.current_container_id == "beef12345678"


async def test_enter_container_external_suspended_is_reused(_patched_container_primitives):
    """外部容器同样走复用（省掉注入与重新拉起）。"""
    driver = _Driver()
    suspended = _FakeClient()
    driver._suspended_clients["deadbeef0002"] = suspended
    _patched_container_primitives["target"] = _EXTERNAL
    _patched_container_primitives["state"] = ContainerState.RUNNING

    await driver.enter_container("deadbeef0002")

    assert driver._client is suspended
    assert driver.connect_calls == []
    assert _patched_container_primitives["prepare_calls"] == []


async def test_enter_container_dead_suspended_cleans_and_falls_back(_patched_container_primitives):
    """挂起会话已死（容器被外部停掉）：清掉死对象，回退正常流程并按原错误码报错。"""
    driver = _Driver()
    dead = _FakeClient(alive=False)
    driver._suspended_clients["c0ffee123456"] = dead
    # 自建容器仍 running ⇒ 正常流程判「异常残留」，错误码必须是原来那个
    _patched_container_primitives["target"] = _MANAGED
    _patched_container_primitives["state"] = ContainerState.RUNNING

    with pytest.raises(TogoException) as excinfo:
        await driver.enter_container("c0ffee123456")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ALREADY_RUNNING
    assert dead.disconnect_calls == 1
    assert driver._suspended_clients == {}


async def test_enter_container_dead_suspended_falls_back_to_normal_start(_patched_container_primitives):
    """挂起会话已死但容器可正常进入（exited）：回退到 disconnect + connect 的原有流程。"""
    driver = _Driver()
    dead = _FakeClient(alive=False)
    driver._suspended_clients["c0ffee123456"] = dead
    _patched_container_primitives["target"] = _MANAGED
    _patched_container_primitives["state"] = ContainerState.EXITED

    target, _state = await driver.enter_container("c0ffee123456")

    assert target.is_managed is True
    assert dead.disconnect_calls == 1
    assert driver.connect_calls == [(ExecEnv.CONTAINER, "c0ffee123456")]
    assert driver.current_container_id == "c0ffee123456"


async def test_enter_container_resolve_failure_evicts_suspended(_patched_container_primitives):
    """容器已被外部删除（resolve 抛 `container_lost`）时，挂起记录必须一并清掉。

    这是 §3.3 步骤 2 的另一半：`_resume_suspended` 的健康检测在 resolve **之后**，
    所以「容器都查不到」这条路径根本走不到它——不在这里淘汰，死 client 与其管道 fd
    就永久留在池里（36 真容器实测过：错误码对，但池里还留着那个 TSPClient）。
    """
    driver = _Driver()
    orphan = _FakeClient()
    driver._suspended_clients["c0ffee123456"] = orphan
    _patched_container_primitives["resolve_error"] = TogoException(
        "容器已不存在", containerService.ERROR_CONTAINER_LOST
    )

    with pytest.raises(TogoException) as excinfo:
        await driver.enter_container("c0ffee123456")

    # 错误码原样透出（不能被清理动作改动）
    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_LOST
    assert driver._suspended_clients == {}
    assert orphan.disconnect_calls == 1
    # 失败路径不该顺手连任何环境
    assert driver.connect_calls == []


async def test_shutdown_clears_all_suspended_clients():
    """`shutdown` 必须清空挂起池：漏掉就是孤儿进程 + 自建容器一直 running。"""
    driver = _Driver()
    client_a = _FakeClient()
    client_b = _FakeClient()
    driver._suspended_clients = {"c0ffee123456": client_a, "beef12345678": client_b}

    await driver.shutdown()

    assert driver._suspended_clients == {}
    assert client_a.disconnect_calls == 1
    assert client_b.disconnect_calls == 1
