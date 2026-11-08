"""#172 判据：`staple_with_retry` 的有限重试行为 + `notarize_dmg` 的调用契约。

`tauri/scripts/build_desktop.py` 不在 `src/` 下、也不被任何既有测试引用，
所以这里按文件路径直接 import 该脚本（它不是包，没有 `__init__.py`）。

覆盖：
  R1  首次成功 → 只调用一次（不得多跑）
  R2  一次失败一次成功 → 恰好两次，且成功
  R3  三次全失败 → 抛出 CalledProcessError 且 rc 保留（不得降级为 warning）
  R4  恰好尝试 3 次（不是无限重试）
  R4-B attempts 参数真的被用（不是写死 3）
  R5  退避间隔真的被 sleep（不是只打日志）
  R5-B 默认退避为正（默认值改 0 等于紧循环连打）
  R6  【行为级】staple 3 次全失败时，notarytool submit 只被调用 1 次
  R7  命令形态与原实现一致（`xcrun stapler staple <target>`）
  R7-B 重试那几次的命令形态与首次完全相同（不得第 2 次换成 validate）
  R8  【行为级】notarize_dmg 确实通过 staple_with_retry 走 staple，
      且恰好一次、参数是 target（守「抽了函数没接调用点」/「调用被注释」/「包进 if False」）
  R9-A/R9-B 【行为级】日志可事后区分「第 N 次成功」与「连续 N 次失败」（硬要求 3）
  R10 docstring 把幂等性标为未验证假设，不得写成结论
  R11 【行为级】notarytool submit 自身失败不得被重试（硬要求 1 的真判据，R6 不覆盖此形态）
  R12 票据校验必须发生在 staple 之后（顺序）
  R13 非 CalledProcessError（如 xcrun 不存在）不得被重试（守 `except Exception` 宽捕获）

⚠️ CI 平台：`.github/workflows/test.yml` 是 `runs-on: ubuntu-latest`，而
`notarize_dmg` 首行有 `if sys.platform != "darwin": raise SystemExit(...)`。
R6/R8/R11/R12 走 `notarize_dmg`，故 `_wire_notarize` 里用
`monkeypatch.setattr(module.sys, "platform", "darwin")` 在测试内声明平台，
**不改生产代码的平台检查**（那个检查是对的）。改后须在非 darwin 下复跑。

R6/R8 是行为级替身断言，不是源码文本断言：
  早期版本用 `inspect.getsource` 文本匹配，对「包进 `if False:`」「调用被注释」
  完全不敏感（实测仍 8 passed），且 R6 断的是「函数体内命令不含 notarytool」，
  与「notarize_dmg 里是否重试 notarytool」正交 —— 把 notarytool submit 包进
  `for range(1, 4)` 时判据照样全绿。故全部换成替身 + 调用计数。

⚠️ 路径坑：`tmp_path` 的目录名含测试函数名，函数名里有 `notarytool` 会被
自己的路径误命中导致假红。故统一用 `tempfile.mkdtemp(prefix="p172_safe_")`。
"""
import importlib.util
import inspect
import os
import subprocess
import tempfile

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


# ── staple_with_retry 单元行为 ───────────────────────────────────────────────


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


def test_r4b_attempts_parameter_is_honored(bd, monkeypatch):
    """R4-B：attempts 参数必须真的被用（不是写死 3）。"""
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET, attempts=5, backoff_seconds=1)
    assert len(bd._test_state["calls"]) == 5, (
        f"attempts=5 时应尝试 5 次，实得 {len(bd._test_state['calls'])} 次"
    )


def test_r5_backoff_actually_sleeps(bd, monkeypatch):
    """R5：退避间隔真的被 sleep（3 次全失败 → 前两次各 sleep 一次）。"""
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET, backoff_seconds=7)
    assert bd._test_state["sleeps"] == [7, 7], (
        f"应有 2 次退避等待（3 次尝试之间），实得 {bd._test_state['sleeps']}"
    )


def test_r5b_default_backoff_is_nonzero(bd, monkeypatch):
    """R5-B：不传 backoff_seconds 时默认退避必须 > 0（硬要求是「有限重试 + 退避」）。

    默认值改成 0 就等于紧循环连打 3 次，网络抖动场景下毫无意义。
    """
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET)
    sleeps = bd._test_state["sleeps"]
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps), (
        f"默认退避应为正数且有 2 次（3 次尝试之间），实得 {sleeps}"
    )


def test_r7_command_shape_unchanged(bd):
    """R7：命令形态与原实现一致 —— `xcrun stapler staple <target>`。"""
    bd.staple_with_retry(TARGET)
    assert bd._test_state["calls"][0] == ["xcrun", "stapler", "staple", TARGET]


def test_r7b_retry_attempts_use_same_command_shape(bd, monkeypatch):
    """R7-B：重试那几次的命令形态必须与首次完全相同（不得第 2 次偷偷换成 validate）。"""
    _fail_on(monkeypatch, bd, fail_times=2)
    bd.staple_with_retry(TARGET)
    expected = ["xcrun", "stapler", "staple", TARGET]
    assert bd._test_state["calls"] == [expected, expected, expected], (
        f"3 次尝试的命令形态应完全相同，实得 {bd._test_state['calls']}"
    )


# ── notarize_dmg 调用契约（行为级替身，非源码文本断言）──────────────────────


def _wire_notarize(monkeypatch, module, staple_fails: int):
    """把 notarize_dmg 的依赖全部换成替身，返回可观测的调用记录。

    staple 行为：前 staple_fails 次调用抛 CalledProcessError，之后成功。
    """
    records = {"notarytool": 0, "staple": [], "verify": 0, "order": []}

    # CI 跑在 ubuntu-latest（test.yml runs-on: ubuntu-latest），而 notarize_dmg
    # 有 `if sys.platform != "darwin": raise SystemExit(...)` 的平台检查。
    # 测试内声明成 darwin，不改生产代码的平台检查。
    monkeypatch.setattr(module.sys, "platform", "darwin")

    # DMG 路径刻意不含 "notarytool" 字样，避免路径被误命中
    workdir = tempfile.mkdtemp(prefix="p172_safe_")
    fake_dmg = os.path.join(workdir, "TogoSpace_0.5.0_x86_64.dmg")
    with open(fake_dmg, "wb") as f:
        f.write(b"fake dmg")

    monkeypatch.setattr(module, "resolve_signing_config", lambda: {
        "apple_id": "a@example.com",
        "app_specific_password": "pw",
        "team_id": "TEAMID",
        "signing_identity_hash": "HASH",
    })
    monkeypatch.setattr(module, "dmg_path", lambda arch: fake_dmg)
    def _fake_verify(target):
        records["verify"] += 1
        records["order"].append("verify")

    monkeypatch.setattr(module, "verify_stapled_ticket", _fake_verify)

    def _fake_run_command(command, cwd=None, env=None, timeout=None):
        if "notarytool" in command:
            records["notarytool"] += 1
            records["order"].append("notarytool")

    monkeypatch.setattr(module, "run_command", _fake_run_command)

    def _fake_staple(target, attempts=3, backoff_seconds=5):
        records["staple"].append(target)
        records["order"].append("staple")
        if len(records["staple"]) <= staple_fails:
            raise subprocess.CalledProcessError(68, ["xcrun", "stapler", "staple", target])

    monkeypatch.setattr(module, "staple_with_retry", _fake_staple)
    return records, fake_dmg


def test_r6_notarytool_submitted_exactly_once_when_staple_always_fails(bd, monkeypatch):
    """R6：staple 3 次全失败时，notarytool submit 只被调用 1 次。

    覆盖范围：这类（如有人把 notarytool 包进**无条件**的 `for range(1,4)`，notarytool 会被调 3 次，这里立刻红）。
    **不覆盖**：`notarytool` 自身失败后的重试循环（`for` + `try: run_command(...); break` + `except` 形态）。
    盲的原因**不在被测代码，在本判据的替身**：`_wire_notarize` 里的 `run_command` 替身从不抛异常 ⇒
    notarytool 永远第一次就成功 ⇒ 循环立刻 break ⇒ 计数恒为 1 ⇒ 本判据仍绿。
    （实测 2×2：形态 A + 替身不让失败 → R6 passed（盲）；形态 A + 替身让失败 → R6 failed，assert 3 == 1。）
    此形态由 **R11** 覆盖——R11 让 notarytool 自己失败，能咬住这种重试循环。
    ⇒ R6 与 R11 互补，共同构成硬要求 1 的自动判据。
    ⚠️ 本段机制描述**没有任何判据在守，只能靠人读**（R10 只守 `staple_with_retry` 的幂等假设，不守这句）；
    改回错误描述不会让任何用例变红。
    """
    records, _ = _wire_notarize(monkeypatch, bd, staple_fails=99)

    with pytest.raises(subprocess.CalledProcessError):
        bd.notarize_dmg("x86_64")

    assert records["notarytool"] == 1, (
        f"notarytool submit 必须只提交 1 次（重复提交浪费公证额度），实得 {records['notarytool']} 次"
    )


def test_r8_notarize_goes_through_staple_with_retry_exactly_once(bd, monkeypatch):
    """R8：notarize_dmg 确实通过 staple_with_retry 走 staple，恰好一次、参数是 target。

    行为级替身：把 staple_with_retry 换成记录器。
    - 「调用被注释掉」→ records['staple'] 为空 → 红
    - 「包进 `if False:`」→ 同上 → 红
    - 「绕过函数直接 run_command」→ 同上 → 红
    """
    records, fake_dmg = _wire_notarize(monkeypatch, bd, staple_fails=0)

    bd.notarize_dmg("x86_64")

    assert records["staple"] == [fake_dmg], (
        f"notarize_dmg 应通过 staple_with_retry 对 target 调用恰好一次，实得 {records['staple']}"
    )
    assert records["verify"] == 1, (
        f"staple 之后必须做票据校验，实得 {records['verify']} 次"
    )


# ── 日志可区分（硬要求 3）────────────────────────────────────────────────────


def test_r9_log_distinguishes_attempt_number_on_eventual_success(bd, monkeypatch, capsys):
    """R9-A：第 2 次才成功时，日志必须写明「第 2 次」——不能只说成功。"""
    _fail_on(monkeypatch, bd, fail_times=1)
    bd.staple_with_retry(TARGET)
    out = capsys.readouterr().out

    assert "第 1/3 次失败" in out, f"失败日志应写明第几次/共几次，实得日志：{out!r}"
    assert "第 2 次尝试成功" in out, (
        f"重试成功日志应写明是第几次才成功（否则事后分不清是首次成功还是重试成功），实得：{out!r}"
    )


def test_r9_log_distinguishes_total_failure(bd, monkeypatch, capsys):
    """R9-B：3 次全失败时，日志必须写明「连续 3 次失败」，且进 stderr。"""
    _fail_on(monkeypatch, bd, fail_times=99)
    with pytest.raises(subprocess.CalledProcessError):
        bd.staple_with_retry(TARGET)
    captured = capsys.readouterr()

    assert "第 1/3 次失败" in captured.out and "第 2/3 次失败" in captured.out, (
        f"前两次失败应逐次写明，实得 stdout：{captured.out!r}"
    )
    assert "连续 3 次失败" in captured.err, (
        f"全失败应在 stderr 写明连续失败次数，实得 stderr：{captured.err!r}"
    )


def test_r10_docstring_marks_idempotency_as_unverified_assumption():
    """R10：docstring 不得把「staple 幂等」写成结论，必须标为未验证假设。

    背景：小马哥指出「重试只发生在失败之后，『已成功 staple』的前提不存在」，
    真正未验证的是「attempt 1 失败但已部分写票时重试是否安全」。
    留一句「重试是幂等的」等于给后人一个假契约。
    """
    module = _load_build_desktop()
    doc = inspect.getdoc(module.staple_with_retry) or ""

    assert "假设" in doc and "未验证" in doc, (
        f"docstring 应把幂等性标为未验证假设，实得：{doc!r}"
    )
    assert "重试是幂等的：" not in doc, (
        "docstring 不得把幂等性写成肯定结论（旧措辞「重试是幂等的：…」应已移除）"
    )


# ── 硬要求 1 的真判据：notarytool 自身失败不得被重试 ─────────────────────────

def test_r11_notarytool_failure_is_not_retried(bd, monkeypatch):
    """R11：notarytool submit 自身失败时必须立刻抛出，且只提交 1 次（硬要求 1）。

    这条与 R6 互补：R6 只覆盖「staple 触发 notarytool」的场景，
    对「notarytool 自身失败被重试」不敏感（实测把 submit 包进 `for range(1,4)`
    时 R6 仍绿）。这里让 notarytool 自己失败，断言它只被调 1 次。
    """
    records, _ = _wire_notarize(monkeypatch, bd, staple_fails=0)

    def _failing_run_command(command, cwd=None, env=None, timeout=None):
        if "notarytool" in command:
            records["notarytool"] += 1
            records["order"].append("notarytool")
            raise subprocess.CalledProcessError(1, list(command))

    monkeypatch.setattr(bd, "run_command", _failing_run_command)

    with pytest.raises(subprocess.CalledProcessError):
        bd.notarize_dmg("x86_64")

    assert records["notarytool"] == 1, (
        f"notarytool submit 自身失败不得重试（重复提交浪费公证额度），实得 {records['notarytool']} 次"
    )
    assert records["staple"] == [], "notarytool 未成功就不该走到 staple"


def test_r12_verify_runs_after_staple_not_before(bd, monkeypatch):
    """R12：票据校验必须发生在 staple 之后（先校验后 staple 等于校验了个空）。"""
    records, _ = _wire_notarize(monkeypatch, bd, staple_fails=0)

    bd.notarize_dmg("x86_64")

    assert records["order"] == ["notarytool", "staple", "verify"], (
        f"调用顺序应为 notarytool → staple → verify，实得 {records['order']}"
    )


def test_r13_non_calledprocesserror_is_not_retried(bd, monkeypatch):
    """R13：`run_command` 抛非 CalledProcessError（如 FileNotFoundError）时必须立刻冒出。

    守 `except Exception` 宽捕获：宽捕获会把「xcrun 不存在」这类配置错误
    也重试 3 次再抛，掩盖真因、且多跑 2 次。
    """
    calls = []

    def _raise_fnf(command, cwd=None, env=None, timeout=None):
        calls.append(list(command))
        raise FileNotFoundError("xcrun")

    monkeypatch.setattr(bd, "run_command", _raise_fnf)

    with pytest.raises(FileNotFoundError):
        bd.staple_with_retry(TARGET)

    assert len(calls) == 1, (
        f"非 CalledProcessError 不应被重试（配置错误不是网络抖动），实得调用 {len(calls)} 次"
    )
