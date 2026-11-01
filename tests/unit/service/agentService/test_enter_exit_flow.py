"""进入 / 退出编排测试（§5.1 / §5.2）。

执行环境属于 Agent，编排在 `agentService`；`containerService` 只提供
「解析目标 / 查状态 / 判定 / 注入」这些容器原语，这里全部打桩。

Driver 用假对象（只记录调用顺序），因此这里断言的是**编排顺序与失败处理**，
不含真实的 stdio 连接——连接本身已由 `test_tsp_driver_container.py` 覆盖。

关键验收点：
- 外部容器进入前必须注入 gtsp，自建容器不重复注入；
- `disconnect` 必须在 `connect(CONTAINER)` 之前；
- 进入失败必须把宿主连接恢复出来（否则 Agent 失去可用环境）；
- 退出失败必须保持容器环境（不能假装成功）。
"""

from __future__ import annotations

import pytest

from constants import ContainerState, ExecEnv
from exception import TogoException
from model.coreModel import ContainerTarget
from service import containerService
from service.agentService import core

_MANAGED = ContainerTarget(container_id="c0ffee123456", is_managed=True, arch="arm64")
_EXTERNAL = ContainerTarget(container_id="deadbeef0002", is_managed=False, arch="arm64")


class _FakeDriver:
    """记录调用顺序的假 Driver（只实现编排用到的四个成员）。"""

    def __init__(self, *, connect_results: list[bool] | None = None, start_env: ExecEnv = ExecEnv.HOST,
                 start_container_id: str | None = None) -> None:
        self.exec_env = start_env
        self.current_container_id = start_container_id
        self.events: list[str] = []
        # 默认全部连接成功；按顺序消费
        self._connect_results = list(connect_results or [])

    async def disconnect(self) -> None:
        self.events.append("disconnect")
        # 真实 Driver 的 disconnect 不改环境，这里保持一致

    async def connect(self, env: ExecEnv = ExecEnv.HOST, container=None) -> bool:
        self.events.append(f"connect:{env.value}:{container.container_id if container else '-'}")
        ok = self._connect_results.pop(0) if self._connect_results else True
        if ok:
            self.exec_env = env
            self.current_container_id = container.container_id if container else None
        return ok


@pytest.fixture
def patched(monkeypatch):
    """打桩容器原语与 Driver 查询，只留 `agentService` 的编排逻辑被测。

    返回一个可配置的控制器：`set_target()` / `set_state()` / `use_driver()`
    决定后续行为，`prepared()` 返回被注入过的目标列表。
    """
    state = {"state": ContainerState.CREATED, "target": _MANAGED, "prepare_calls": [], "driver": None}

    async def _fake_get_target(agent_id, container_id):
        return state["target"]

    async def _fake_get_state(container_id):
        return state["state"]

    async def _fake_prepare(target):
        state["prepare_calls"].append(target)

    monkeypatch.setattr(containerService, "get_container_target", _fake_get_target)
    monkeypatch.setattr(containerService, "get_container_state", _fake_get_state)
    monkeypatch.setattr(containerService, "prepare_container_gtsp", _fake_prepare)
    monkeypatch.setattr(core, "_get_loaded_driver", lambda _agent_id: state["driver"])

    class _Control:
        def set(self, **kwargs):
            state.update(kwargs)

        def use_driver(self, driver):
            state["driver"] = driver

        def prepare_calls(self):
            return state["prepare_calls"]

    return _Control()


# ---------------------------------------------------------------------------
# enter_container
# ---------------------------------------------------------------------------


async def test_enter_managed_container_from_created_disconnects_then_connects(patched):
    """自建容器从 `created` 进入：先 disconnect 再 connect(CONTAINER)，不注入。

    注入这一步在 `create_container` 时已经做过，重复拷 6MB 没有意义。
    """
    patched.set(target=_MANAGED, state=ContainerState.CREATED)
    driver = _FakeDriver()
    patched.use_driver(driver)

    target, state = await core.enter_container(1, "c0ffee123456")

    assert state is ContainerState.CREATED
    assert driver.events == ["disconnect", "connect:container:c0ffee123456"]
    assert patched.prepare_calls() == []


async def test_enter_managed_container_from_exited_is_allowed(patched):
    """`exited` 也是合法稳态（上次会话结束后的常态），必须能再次进入。"""
    patched.set(target=_MANAGED, state=ContainerState.EXITED)
    driver = _FakeDriver()
    patched.use_driver(driver)

    await core.enter_container(1, "c0ffee123456")

    assert driver.events == ["disconnect", "connect:container:c0ffee123456"]


async def test_enter_external_container_prepares_gtsp_before_connecting(patched):
    """外部容器必须**先注入再连接**，顺序不能反（连上后容器内没有可执行的 gtsp）。"""
    patched.set(target=_EXTERNAL, state=ContainerState.RUNNING)
    driver = _FakeDriver()
    patched.use_driver(driver)

    await core.enter_container(1, "deadbeef0002")

    assert patched.prepare_calls() == [_EXTERNAL]
    assert driver.events == ["disconnect", "connect:container:deadbeef0002"]


async def test_enter_managed_running_is_rejected_without_touching_connection(patched):
    """自建容器 `running` ⇒ `container_already_running`，且**不切换连接**。

    不切连接很重要：失败时若已经断开旧环境，Agent 就掉进「没有可用环境」的坑。
    """
    patched.set(target=_MANAGED, state=ContainerState.RUNNING)
    driver = _FakeDriver()
    patched.use_driver(driver)

    with pytest.raises(TogoException) as excinfo:
        await core.enter_container(1, "c0ffee123456")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ALREADY_RUNNING
    assert driver.events == []


async def test_enter_external_stopped_is_rejected(patched):
    """外部容器非 running ⇒ `container_stopped`，同样不切连接、不注入。"""
    patched.set(target=_EXTERNAL, state=ContainerState.EXITED)
    driver = _FakeDriver()
    patched.use_driver(driver)

    with pytest.raises(TogoException) as excinfo:
        await core.enter_container(1, "deadbeef0002")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_STOPPED
    assert driver.events == []
    assert patched.prepare_calls() == []


async def test_enter_reports_enter_failed_when_state_is_unknown(patched):
    """状态查不到（unknown）⇒ `container_enter_failed`，不当成 missing 或放行。"""
    patched.set(target=_MANAGED, state=ContainerState.UNKNOWN)
    driver = _FakeDriver()
    patched.use_driver(driver)

    with pytest.raises(TogoException) as excinfo:
        await core.enter_container(1, "c0ffee123456")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ENTER_FAILED
    assert driver.events == []


async def test_enter_is_idempotent_when_already_in_same_container(patched):
    """已在目标容器 ⇒ 直接成功，**不重连**（重连会打断正在进行的 turn）。"""
    patched.set(target=_MANAGED, state=ContainerState.RUNNING)
    driver = _FakeDriver(start_env=ExecEnv.CONTAINER, start_container_id="c0ffee123456")
    patched.use_driver(driver)

    await core.enter_container(1, "c0ffee123456")

    assert driver.events == []


async def test_enter_failure_restores_host_connection(patched):
    """进入失败必须把宿主连接恢复出来，而不是把 Agent 留在「无连接」状态。

    这是 §4.3 明确要求的「该操作自身的失败处理」——与运行中的自动回落不同：
    这里是一次失败的进入操作，必须把 Agent 恢复到可用状态。
    """
    patched.set(target=_MANAGED, state=ContainerState.CREATED)
    # connect(CONTAINER) 失败，connect(HOST) 成功
    driver = _FakeDriver(connect_results=[False, True])
    patched.use_driver(driver)

    with pytest.raises(TogoException) as excinfo:
        await core.enter_container(1, "c0ffee123456")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ENTER_FAILED
    assert driver.events == [
        "disconnect",
        "connect:container:c0ffee123456",
        "connect:host:-",
    ]
    assert driver.exec_env == ExecEnv.HOST


async def test_enter_failure_does_not_mask_error_when_host_restore_also_fails(patched):
    """宿主也恢复不了时仍抛「进入失败」，不因二次失败改写错误类型或吞掉异常。"""
    patched.set(target=_MANAGED, state=ContainerState.CREATED)
    driver = _FakeDriver(connect_results=[False, False])
    patched.use_driver(driver)

    with pytest.raises(TogoException) as excinfo:
        await core.enter_container(1, "c0ffee123456")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ENTER_FAILED


async def test_enter_rejects_agent_without_loaded_driver(patched):
    """Agent 未加载（或不是 TSP driver）⇒ 明确拒绝，不去碰容器。"""
    with pytest.raises(TogoException) as excinfo:
        await core.enter_container(1, "c0ffee123456")

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_INVALID_PARAMS
    assert patched.prepare_calls() == []


# ---------------------------------------------------------------------------
# exit_to_host
# ---------------------------------------------------------------------------


async def test_exit_disconnects_container_then_connects_host(patched):
    """退出顺序：先 disconnect（关 stdin → 容器停回 exited）再 connect(HOST)。"""
    driver = _FakeDriver(start_env=ExecEnv.CONTAINER, start_container_id="c0ffee123456")
    patched.use_driver(driver)

    await core.exit_to_host(1)

    assert driver.events == ["disconnect", "connect:host:-"]
    assert driver.exec_env == ExecEnv.HOST


async def test_exit_from_host_is_idempotent(patched):
    """已在宿主 ⇒ 幂等成功，不重连（避免打断正在进行的 turn）。"""
    driver = _FakeDriver(start_env=ExecEnv.HOST)
    patched.use_driver(driver)

    await core.exit_to_host(1)

    assert driver.events == []


async def test_exit_failure_keeps_container_env(patched):
    """宿主连接失败 ⇒ 抛错且**环境仍是容器**，由 Agent 重试 exit_to_host。

    不能因 connect(HOST) 失败就把环境标成 HOST：那会让后续工具打到宿主，
    而 Agent 以为自己还在容器里。
    """
    driver = _FakeDriver(start_env=ExecEnv.CONTAINER, start_container_id="c0ffee123456")
    driver._connect_results = [False]
    patched.use_driver(driver)

    with pytest.raises(TogoException) as excinfo:
        await core.exit_to_host(1)

    assert excinfo.value.error_code == containerService.ERROR_CONTAINER_ENTER_FAILED
    assert driver.exec_env == ExecEnv.CONTAINER
    assert driver.current_container_id == "c0ffee123456"
