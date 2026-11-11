"""前端日志桥（#197）的写盘模块单测：开关口径、清洗、时间戳、落盘与不落盘。

这些用例**不启后端进程**：`util.frontendLog` 是纯函数 + 一个 logger，直接调即可。
真正「前端报文经 ws 到文件」的端到端读数在
`tests/api/test_ws_frontend_log/test.py`，两者分工不同 ——
这里钉的是「什么该写、什么不该写」，那里钉的是「真的有东西到了盘上」。
"""

from __future__ import annotations

import logging
import os

import pytest

from util import frontendLog, logUtil


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """把 `LOGS_DIR` 指到临时目录，并重建本模块的 handler。"""
    monkeypatch.setattr(frontendLog.logUtil.appPaths, "LOGS_DIR", str(tmp_path))
    frontendLog._reset_sink_for_tests()
    yield tmp_path
    frontendLog._reset_sink_for_tests()


@pytest.fixture
def debug_on(monkeypatch):
    monkeypatch.setenv(frontendLog.ENV_FRONTEND_DEBUG, "1")


@pytest.fixture
def debug_off(monkeypatch):
    monkeypatch.delenv(frontendLog.ENV_FRONTEND_DEBUG, raising=False)


def _read(path) -> str:
    assert os.path.exists(path), f"日志文件未生成: {path}"
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------- #
# 1. 开关取值口径（必须与 Rust `frontend_debug_enabled()` 完全一致）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("True", True),
        (" 1 ", True),
        (" true\n", True),
        ("0", False),
        ("false", False),
        ("yes", False),  # 外壳不认 yes，这里也不能认，否则半开
        ("on", False),
        ("", False),
        ("2", False),
    ],
)
def test_frontend_debug_enabled_matches_shell_semantics(raw, expected):
    """取值口径与 Tauri 外壳一致：只认 `1` / `true`（大小写不敏感、允许空白）。"""
    assert frontendLog.frontend_debug_enabled({frontendLog.ENV_FRONTEND_DEBUG: raw}) is expected


def test_frontend_debug_disabled_when_env_missing():
    assert frontendLog.frontend_debug_enabled({}) is False


# --------------------------------------------------------------------------- #
# 2. 清洗与时间戳
# --------------------------------------------------------------------------- #


def test_sanitize_escapes_newlines_so_client_cannot_forge_log_lines():
    """换行必须转义：日志是行导向的，正文里的换行等于让客户端伪造整行。"""
    cleaned = frontendLog._sanitize("a\n2026-01-01 ERROR fake\r\nb")
    assert "\n" not in cleaned
    assert "\r" not in cleaned
    assert cleaned == "a\\n2026-01-01 ERROR fake\\nb"


def test_sanitize_strips_control_chars():
    """制表以外的控制字符（含 ESC、NUL）一律剔除，避免终端转义与编码问题。"""
    assert frontendLog._sanitize("a\x00b\x1bc\x07d") == "abcd"


def test_sanitize_truncates_long_message():
    long_message = "x" * (frontendLog.MAX_MESSAGE_CHARS + 10)
    cleaned = frontendLog._sanitize(long_message)
    assert cleaned.startswith("x" * 100)
    assert "truncated 10 chars" in cleaned
    # 截断后不再无限增长
    assert len(cleaned) < frontendLog.MAX_MESSAGE_CHARS + 40


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, "1970-01-01T00:00:00.000Z"),
        (1700000000123, "2023-11-14T22:13:20.123Z"),
        (1700000000123.9, "2023-11-14T22:13:20.123Z"),
        ("1700000000123", "-"),  # 字符串不认，避免字符串数字蒙混
        (None, "-"),
        (True, "-"),  # bool 是 int 子类，必须显式排除
    ],
)
def test_format_frontend_ts(raw, expected):
    assert frontendLog._format_frontend_ts(raw) == expected


# --------------------------------------------------------------------------- #
# 3. 开关关闭 ⇒ 不写盘、不建文件
# --------------------------------------------------------------------------- #


def test_switch_off_writes_nothing_and_creates_no_file(log_dir, debug_off):
    """开关关闭：返回 False，且**盘上不出现任何文件**。

    「不出现文件」是交付标准 2 的原话，不是「文件存在但为空」—— 后者同样会在用户
    机器上留下痕迹，而且会被误判成「开关打开了但没日志」。
    """
    assert frontendLog.handle_message({"type": "frontend_log", "message": "hello"}) is False
    assert list(log_dir.rglob("*")) == []


def test_backend_startup_does_not_create_frontend_log(log_dir, debug_off):
    """后端常规启动（`init_backend_logger`）也不得建出 frontend.log。

    这条防的是「把条目当普通条目登记」那种写法：`RotatingFileHandler` 一构造就建
    文件，于是开关关着的机器上也会多出一个空文件。本模块的条目因此带 `lazy=True`。
    """
    logUtil.init_backend_logger(str(log_dir))
    assert not (log_dir / "frontend" / "frontend.log").exists()
    # 正向对照：其它条目的文件**确实**在这一步被建出来了，否则本用例可能是空过。
    assert (log_dir / "controller" / "controller.log").exists()


# --------------------------------------------------------------------------- #
# 4. 开关打开 ⇒ 写盘
# --------------------------------------------------------------------------- #


def test_switch_on_writes_line_with_level_client_and_frontend_ts(log_dir, debug_on):
    """开关打开：写一行，含级别、连接标识、前端时间戳与正文。"""
    wrote = frontendLog.handle_message(
        {
            "type": "frontend_log",
            "level": "warn",
            "message": "后端收到的第一条前端日志",
            "ts": 1700000000123,
        },
        client="127.0.0.1:51234",
    )
    assert wrote is True

    content = _read(log_dir / "frontend" / "frontend.log")
    assert "[frontend] level=warn" in content
    assert "client=127.0.0.1:51234" in content
    assert "frontend_ts=2023-11-14T22:13:20.123Z" in content
    assert "后端收到的第一条前端日志" in content
    # 后端接收时间也留着：只有「后端收晚」和「前端卡住」的差异才需要两个时间。
    assert content.split(" - ")[0][:4].isdigit()


def test_switch_on_maps_levels_to_log_levels(log_dir, debug_on, caplog):
    """级别映射：error→ERROR、warn→WARNING、debug→DEBUG、其余→INFO。

    用真实记录对象断言，而不是只看文本：文本对了但 levelno 错，会让
    `backend_warning.log` 之类的按级别过滤产生错误的收录行为。
    """
    levels = {}
    with caplog.at_level(logging.DEBUG, logger="util.frontendLog"):
        for level in ("error", "warn", "debug", "log", "info"):
            frontendLog.handle_message({"type": "frontend_log", "level": level, "message": level})
    for record in caplog.records:
        if record.name != "util.frontendLog":
            continue
        levels[record.getMessage().split("level=")[1].split(" ")[0]] = record.levelno

    assert levels == {
        "error": logging.ERROR,
        "warn": logging.WARNING,
        "debug": logging.DEBUG,
        "log": logging.INFO,
        "info": logging.INFO,
    }


def test_unknown_level_falls_back_to_log(log_dir, debug_on):
    """非法级别：降级成 `log`，仍然写盘（丢掉整条日志得不偿失）。"""
    assert frontendLog.handle_message(
        {"type": "frontend_log", "level": "level=error", "message": "x"}
    )
    content = _read(log_dir / "frontend" / "frontend.log")
    assert "level=log" in content
    # 客户端不能靠 level 往行里塞内容
    assert "level=level=error" not in content


# --------------------------------------------------------------------------- #
# 5. 非法输入 ⇒ 不写、不抛
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "frontend_log", "message": 123},
        {"type": "frontend_log", "message": None},
        {"type": "frontend_log", "message": ["a"]},
        {"type": "frontend_log", "message": ""},
        {"type": "frontend_log", "message": "\n\r"},
        {"type": "frontend_log"},  # 没有 message
    ],
)
def test_invalid_payload_returns_false_without_writing(log_dir, debug_on, payload):
    """非法载荷只返回 False，不写盘、不抛异常。

    这条链路跑在 ws 的 `on_message` 回调里，抛异常会连带影响同一连接上的业务事件推送。
    """
    assert frontendLog.handle_message(payload) is False
    path = log_dir / "frontend" / "frontend.log"
    if path.exists():
        assert path.read_text(encoding="utf-8").strip() == ""


def test_non_string_message_rejected_rather_than_stringified(log_dir, debug_on):
    """非字符串 message 直接丢弃，不用 `str()` 兜底。

    `str({"a": 1})` 出来的是 Python repr，和前端在 devtools 里看到的形状不同，
    落盘后会被当成「前端发了这种东西」误导排查。前端负责序列化。
    """
    assert frontendLog.handle_message({"type": "frontend_log", "message": {"a": 1}}) is False
    assert list(log_dir.rglob("*")) == []


def test_handle_message_raises_when_storage_unwritable(tmp_path, monkeypatch, debug_on):
    """存储不可写时**本模块抛异常**，不在这里吞掉 —— 分工是刻意的。

    吞异常会让「开关开了但一条也没写进去」变成静默事实；而抛出去由调用方（ws 控制器）
    统一兜住并记进 `controller.log`，才能既不影响连接、又留下可查的痕迹。
    控制器侧的兜底见 `tests/unit/controller/test_ws_frontend_log.py`。
    """
    (tmp_path / "frontend").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(frontendLog.logUtil.appPaths, "LOGS_DIR", str(tmp_path))
    frontendLog._reset_sink_for_tests()

    with pytest.raises(OSError):
        frontendLog.handle_message({"type": "frontend_log", "message": "x"})
    frontendLog._reset_sink_for_tests()
