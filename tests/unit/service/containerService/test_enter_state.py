"""进入前的状态判定（§1.2 / §4.1）。

这段逻辑被第 5 步（`enter_container` 工具）和第 4 步（Driver 进入流程）共用，
所以单独钉住：自建与外部容器的规则**不同**，合并实现就会错。
"""

from __future__ import annotations

import pytest

from constants import ContainerArch
from exception import TogoException
from model.coreModel import ContainerTarget
from service.containerService import core

_MANAGED = ContainerTarget(container_id="c0ffee123456", is_managed=True, arch=ContainerArch.ARM64)
_EXTERNAL = ContainerTarget(container_id="deadbeef0002", is_managed=False, arch=ContainerArch.ARM64)


# ---------------------------------------------------------------------------
# 自建容器：稳态只有 created / exited
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["created", "exited"])
def test_managed_container_can_be_entered_from_steady_states(state):
    """`created` / `exited` 是自建容器的两个合法稳态，直接允许进入（§4.1）。"""
    core.validate_enter_state(_MANAGED, state)  # 不抛即通过


def test_managed_running_is_rejected_as_abnormal_residue():
    """自建容器空闲时 `running` 是异常残留 ⇒ `container_already_running`。

    关键点：**不能**隐式 stop 或 restart 把它"修好"——那会抹掉「有残留进程」
    这条排查线索（§1.2 风险表明确列为设计选择）。
    """
    with pytest.raises(TogoException) as excinfo:
        core.validate_enter_state(_MANAGED, "running")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_ALREADY_RUNNING


@pytest.mark.parametrize("state", ["paused", "dead", "restarting", "removing", "stopped"])
def test_managed_abnormal_states_report_enter_failed_with_actual_state(state):
    """其他异常状态统一 `container_enter_failed`，且 message 里要有实际状态。

    带实际状态是为了让 Agent/用户能自己判断该删还是该等，而不是看到一句
    「进入失败」无从下手。
    """
    with pytest.raises(TogoException) as excinfo:
        core.validate_enter_state(_MANAGED, state)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_ENTER_FAILED
    assert state in str(excinfo.value)


# ---------------------------------------------------------------------------
# 外部容器：只认 running
# ---------------------------------------------------------------------------


def test_external_running_is_allowed():
    """外部容器 `running` 是正常态，允许进入（形态 B）。"""
    core.validate_enter_state(_EXTERNAL, "running")


@pytest.mark.parametrize("state", ["created", "exited", "paused", "dead", "restarting"])
def test_external_non_running_is_rejected_with_stopped(state):
    """外部容器非 `running` 一律 `container_stopped`，message 带实际状态（§4.1）。"""
    with pytest.raises(TogoException) as excinfo:
        core.validate_enter_state(_EXTERNAL, state)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_STOPPED
    assert state in str(excinfo.value)


def test_managed_and_external_rules_differ_for_the_same_state():
    """**同一状态在两种容器上判定相反**：`exited` 自建可进、外部拒绝。

    这条是防止把两套规则合并成一份实现的护栏——合并后必然有一边是错的。
    """
    core.validate_enter_state(_MANAGED, "exited")  # 自建：允许

    with pytest.raises(TogoException) as excinfo:
        core.validate_enter_state(_EXTERNAL, "exited")  # 外部：拒绝
    assert excinfo.value.error_code == core.ERROR_CONTAINER_STOPPED


def test_missing_and_unknown_states_are_rejected_not_auto_recovered():
    """`missing` / `unknown` 也不能进入，且不触发任何自动恢复动作。

    状态查询失败（unknown）与容器不存在（missing）都由上层给专门错误码，
    这里只保证它们不会「碰巧通过」。
    """
    with pytest.raises(TogoException):
        core.validate_enter_state(_MANAGED, "missing")
    with pytest.raises(TogoException):
        core.validate_enter_state(_EXTERNAL, "unknown")
