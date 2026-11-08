"""#172 判据：`staple_with_retry` 的有限重试行为（自动判据，非代码审查）。

`tauri/scripts/build_desktop.py` 不在 `src/` 下、也不被任何既有测试引用，
所以这里按文件路径直接 import 该脚本（它不是包，没有 `__init__.py`）。

覆盖：
  R1 首次成功 → 只调用一次（不得多跑）
  R2 一次失败一次成功 → 恰好两次，且成功
  R3 三次全失败 → 抛出 CalledProcessError 且 rc 保留（不得降级为 warning）
  R4 恰好尝试 3 次（不是无限重试）
  R5 退避间隔真的被 sleep（不是只打日志）
  R6 只重试 staple，不碰 notarytool submit（源级静态断言）
  R7 命令形态与原实现一致（`xcrun stapler staple <target>`）
"""
import importlib.util
import os
import subprocess

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_REPO_ROOT, "tauri", "scripts", "build_desktop.py")


def _load_build_desktop():
    """按路径加载 build_desktop.py（该目录不是包）。"""
    spec = importlib.util.spec_from_file_location("build_desktop_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None, f"无法加载 {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def bd(monkeypatch):
    """加载脚本，并把 run_command / time.sleep 换成可控桩。"""
    module = _load_build_desktop()

    state = {"calls": [], "sleeps": []}

    def _fake_run_command(command, cwd=None, env=None, timeout=None):
        state["calls"].append(list(command))

    def _fake_sleep(seconds):
        state["sleeps"].append(seconds)

    monkeypatch.setattr(module, "run_command", _fake_run_command)
    monkeypatch.setattr(module.time, "sleep", _fake_sleep)
    module._test_state = state
    return module


def _fail_on(monkeypatch, module, fail_times: int, rc: int = 68):
    """让前 fail_times 次 run_command 抛 CalledProcessError，之后成功。"""
    state = module._test_state
    real_calls = []

    def _fake(command, cwd=None, env=None, timeout=None):
        real_calls.append(list(command))
        state["calls"] = real_calls
        if len(real_calls) <= fail_times:
            raise subprocess.CalledProcessError(rc, command)

    monkeypatch.setattr(module, "run_command", _fake)


TARGET = "/tmp/TogoSpace_0.5.0_x86_64.dmg"


def test_r1_first_attempt_success_runs_exactly_once(bd):
    """R1：首次成功不得多跑（Q4 的要求）。"""
    bd.staple_with_retry(TARGET)
    assert len(bd._test_state["calls"]) == 1, (
        f"首次成功应只调用 1 次，实得 {len(bd._test_state['calls'])} 次"
    )
    assert bd._test_state["sleeps"] == [], "首次成功不应有退避等待"


def test_r2_fail_then_success(bd, monkeypatch):
    """R2：一次失败一次成功 → 恰好两次调用且不抛异常（Q2 的核心举证）。"""
    _fail_on(monkeypatch, bd, fail_times=1)
    bd.staple_with_retry(TARGET)
    assert len(bd._test_state["calls"]) == 2, (
        f"应重试一次共 2 次调用，实得 {len(bd._test_state['calls'])} 次"
    )


def test_r3_all_attempts_fail_raises_not_warns(bd, monkeypatch):
    """R3：3 次全失败必须抛出，不得降级为 warning（发版产物必须带票据）。"""
    _fail_on(monkeypatch, bd, fail_times=99, rc=68)
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        bd.staple_with_retry(TARGET)
    assert exc_info.value.returncode == 68, (
        f"应保留原始 rc=68，实得 {exc_info.value.returncode}"
    )


def test_r4_bounded_attempts_exactly_three(bd, monkeypatch):
    """R4：全失败时恰好尝试 3 次，不是无限重试。"""
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET)
    assert len(bd._test_state["calls"]) == 3, (
        f"应有界 3 次，实得 {len(bd._test_state['calls'])} 次"
    )


def test_r5_backoff_actually_sleeps(bd, monkeypatch):
    """R5：退避间隔真的被 sleep（3 次全失败 → 前两次各 sleep 一次）。"""
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET, backoff_seconds=7)
    assert bd._test_state["sleeps"] == [7, 7], (
        f"应有 2 次退避等待（3 次尝试之间），实得 {bd._test_state['sleeps']}"
    )


def test_r6_only_staple_never_notarytool_submit(bd, monkeypatch):
    """R6：只重试 staple，命令里不得出现 notarytool submit（会浪费公证额度）。"""
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET)
    for command in bd._test_state["calls"]:
        joined = " ".join(command)
        assert "notarytool" not in joined, f"重试路径不该出现 notarytool：{command}"
        assert "submit" not in joined, f"重试路径不该出现 submit：{command}"


def test_r7_command_shape_unchanged(bd):
    """R7：命令形态与原实现一致 —— `xcrun stapler staple <target>`。"""
    bd.staple_with_retry(TARGET)
    assert bd._test_state["calls"][0] == ["xcrun", "stapler", "staple", TARGET]


def test_r8_notarize_dmg_calls_staple_with_retry():
    """R8：`notarize_dmg` 里确实走的是重试版，不是裸 run_command。

    静态断言（源码级）：notarize_dmg 函数体内必须调用 staple_with_retry，
    且不得残留 `run_command(["xcrun", "stapler", "staple", ...])` 的裸调用。
    这条守「抽了函数但调用点没改」这种半改状态。
    """
    import inspect

    module = _load_build_desktop()
    source = inspect.getsource(module.notarize_dmg)
    assert "staple_with_retry(" in source, "notarize_dmg 未调用 staple_with_retry"
    assert 'run_command(["xcrun", "stapler", "staple"' not in source, (
        "notarize_dmg 仍残留裸的 staple 调用，重试没接上"
    )
