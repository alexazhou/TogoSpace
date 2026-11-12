"""ws 控制器对 `frontend_log` 上行报文的处理（#197）。

**不开真实端口**：与 `test_ws_auth_ack.py` 同一套路 —— `object.__new__(EventsWsHandler)`
构造 handler，替换 `_send` / `close` / `_subscribe_events`，断言的是**授权边界**与
**订阅幂等**两件事。落盘细节在 `tests/unit/util/test_frontend_log.py`。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from controller import wsController


def _fake_handler():
    handler = object.__new__(wsController.EventsWsHandler)
    sent: list[str] = []
    closed: list[tuple] = []

    async def _send(payload: str) -> None:
        sent.append(payload)

    handler._send = _send  # type: ignore[method-assign]
    handler.close = lambda code=None, reason=None: closed.append((code, reason))  # type: ignore[method-assign]
    handler.request = SimpleNamespace(remote_ip="127.0.0.1", remote_port=51234)
    return handler, sent, closed


def _auth_config(enabled: bool, token: str = "secret") -> SimpleNamespace:
    return SimpleNamespace(setting=SimpleNamespace(auth=SimpleNamespace(enabled=enabled, token=token)))


@pytest.fixture
def subscribed(monkeypatch):
    """把 `_subscribe_events` 换成打点，记录调用次数。"""
    calls: list[int] = []

    def _record(self):
        calls.append(1)
        self._events_subscribed = True

    monkeypatch.setattr(wsController.EventsWsHandler, "_subscribe_events", _record)
    return calls


@pytest.fixture
def captured(monkeypatch):
    """把 `frontendLog.handle_message` 换成收集器，返回收到的 payload 列表。"""
    calls: list[tuple[dict, str]] = []

    def _record(payload, *, client="-"):  # noqa: ANN001
        calls.append((payload, client))
        return True

    monkeypatch.setattr(wsController.frontendLog, "handle_message", _record)
    return calls


def _log_frame(**overrides) -> str:
    payload = {"type": "frontend_log", "level": "warn", "message": "hi", "ts": 1700000000123}
    payload.update(overrides)
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# 1. 鉴权关闭：连接即已授权
# --------------------------------------------------------------------------- #


def test_auth_disabled_handles_frontend_log(monkeypatch, subscribed, captured):
    """鉴权关闭时 `open()` 即视为已授权，日志必须能落盘。

    浏览器/桌面在鉴权关闭的部署下是主流形态；这条分支如果按「必须已认证」去拦截，
    等于这个功能在最常见的部署方式下完全不可用。
    """
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(False))
    handler, sent, closed = _fake_handler()
    handler._authenticated = True  # open() 里做的

    handler.on_message(_log_frame())

    assert len(captured) == 1
    assert captured[0][0]["message"] == "hi"
    assert captured[0][1] == "127.0.0.1:51234"
    assert closed == []
    assert sent == []


def test_auth_disabled_never_closes_on_bad_payload(monkeypatch, subscribed, captured):
    """鉴权关闭分支保持既有语义：即使报文完全无法解析，也不关连接、不回帧。

    改动前这条分支根本不解析上行内容，任何报文都只是「忽略」。新增解析后若顺手加了
    close，就会变成「某个客户端发了句非 JSON，实时推送就断了」的回归。
    """
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(False))
    handler, _, closed = _fake_handler()
    handler._authenticated = True

    for raw in ("not json at all", "[1,2,3]", "", "null", '"str"'):
        handler.on_message(raw)

    assert closed == []
    assert captured == []


def test_auth_disabled_subscription_is_idempotent(monkeypatch, subscribed, captured):
    """订阅幂等：连发多条日志不能让总线回调成倍堆叠。

    `messageBus.subscribe_many` 是**追加**语义。本单让前端持续发上行，如果不幂等，
    「发 N 条日志」就会变成「业务事件被推 N+1 次」—— 表现为界面消息重复、任务状态
    乱跳，很难联想到日志功能上。
    """
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(False))
    handler, _, _ = _fake_handler()
    handler._authenticated = True
    handler._events_subscribed = False

    for _ in range(5):
        handler.on_message(_log_frame())

    assert len(subscribed) == 1
    assert len(captured) == 5


# --------------------------------------------------------------------------- #
# 2. 鉴权开启：必须认证之后才处理
# --------------------------------------------------------------------------- #


def test_auth_enabled_before_auth_drops_log_and_keeps_connection(monkeypatch, subscribed, captured):
    """未认证时的 `frontend_log`：丢弃、**不断连**。

    前端有可能在 `auth` 之后、`auth_ok` 之前就发出日志（时机窗口很小但存在），也可能
    有客户端顺序写错。为一条日志断掉实时推送，会让「日志越多越连不上」成为极难排查的
    问题；静默丢弃的最坏后果只是丢几条日志。
    """
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(True))
    handler, sent, closed = _fake_handler()
    handler._authenticated = False

    handler.on_message(_log_frame())

    assert captured == []
    assert closed == []
    assert sent == []


async def test_auth_enabled_after_auth_handles_frontend_log(monkeypatch, subscribed, captured):
    """认证之后再发的日志必须落盘。

    需要真事件循环：`_send_auth_ack` 走 `asyncio.create_task`。
    """
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(True, "secret"))
    handler, _, closed = _fake_handler()

    handler.on_message(json.dumps({"type": "auth", "token": "secret"}))
    await asyncio.sleep(0)
    handler.on_message(_log_frame(level="error", message="认证之后的日志"))

    assert closed == []
    assert len(captured) == 1
    assert captured[0][0]["message"] == "认证之后的日志"


def test_auth_enabled_unauthorized_log_does_not_subscribe(monkeypatch, subscribed, captured):
    """未认证的日志报文不能顺带把事件订阅开出来 —— 那等于绕过鉴权拿实时推送。"""
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(True))
    handler, _, _ = _fake_handler()
    handler._authenticated = False

    handler.on_message(_log_frame())

    assert subscribed == []


def test_auth_enabled_wrong_token_still_closes(monkeypatch, subscribed, captured):
    """鉴权启用时，非 auth 报文仍然按既有语义断开（既有测试已覆盖，这里防回归）。"""
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(True, "secret"))
    handler, _, closed = _fake_handler()

    handler.on_message(json.dumps({"type": "whatever"}))

    assert closed == [(1008, "Auth required")]
    assert captured == []


# --------------------------------------------------------------------------- #
# 3. 落盘异常不能打断连接
# --------------------------------------------------------------------------- #


def test_storage_failure_is_swallowed_and_connection_kept(monkeypatch, subscribed):
    """落盘抛异常时：连接保持、不抛给 tornado 的 on_message。

    磁盘满、目录权限变化都可能让写盘失败。这里的取舍是「宁可少一条日志，也不能让
    实时推送跟着挂」。同时必须留下痕迹 —— 断言 warning 被记录。
    """
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(False))

    def _boom(payload, *, client="-"):
        raise OSError("disk full")

    monkeypatch.setattr(wsController.frontendLog, "handle_message", _boom)
    handler, _, closed = _fake_handler()
    handler._authenticated = True

    warnings: list[str] = []

    class _RecordingLogger:
        def warning(self, msg, *args, **kwargs):
            warnings.append(msg)

        def info(self, msg, *args, **kwargs):
            pass

        def debug(self, msg, *args, **kwargs):
            pass

    monkeypatch.setattr(wsController, "logger", _RecordingLogger())

    handler.on_message(_log_frame())

    assert closed == []
    assert any("frontend_log" in message for message in warnings)


# --------------------------------------------------------------------------- #
# 4. 报文分派细节
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps([1, 2, 3]),
        json.dumps("string"),
        json.dumps({"type": "other"}),
        json.dumps({"type": "FRONTEND_LOG"}),
        json.dumps({"level": "warn", "message": "no type"}),
    ],
)
def test_non_frontend_log_is_ignored(monkeypatch, subscribed, captured, raw):
    """非 `frontend_log`（含大小写不同、缺 type）一律不进入落盘。"""
    monkeypatch.setattr(wsController.configUtil, "get_app_config", lambda: _auth_config(False))
    handler, _, closed = _fake_handler()
    handler._authenticated = True

    handler.on_message(raw)

    assert captured == []
    assert closed == []


def test_client_label_falls_back_when_request_missing():
    """没有 `request` 时连接标识退化成 `-`，不抛异常（单测里常没有真实 request）。"""
    handler = object.__new__(wsController.EventsWsHandler)
    assert handler._client_label() == "-"

    handler.request = SimpleNamespace(remote_ip="", remote_port=1)
    assert handler._client_label() == "-"

    handler.request = SimpleNamespace(remote_ip="10.0.0.1", remote_port=None)
    assert handler._client_label() == "10.0.0.1:-"


def test_parse_json_object_returns_none_for_non_dict():
    """`[1,2,3]` / `"str"` / 非法 JSON 都要返回 None，而不是让调用方在 `.get()` 上炸。"""
    assert wsController._parse_json_object("[1,2,3]") is None
    assert wsController._parse_json_object('"str"') is None
    assert wsController._parse_json_object("null") is None
    assert wsController._parse_json_object("{bad") is None
    assert wsController._parse_json_object(None) is None
    assert wsController._parse_json_object(json.dumps({"a": 1})) == {"a": 1}
