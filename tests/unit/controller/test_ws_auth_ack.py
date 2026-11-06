"""WebSocket 认证成功后的 `auth_ok` 确认帧（need_fix_1.md P1-2）。

**缺陷背景**：鉴权开启时，后端认证成功后只订阅事件、不回任何确认帧；前端却把
「收到首条 WS 消息」当作连接就绪信号。总线上没有事件时前端就一直显示「连接中」，
要等某条房间消息恰好推送才变绿。

**不启动真实 tornado 服务**：与 `test_ws_container_event.py` 同一套路，直接构造
handler 并把 `_send` 换成收集器，比开监听端口稳定得多。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from controller import wsController


def _fake_handler() -> tuple[wsController.EventsWsHandler, list[str], list[tuple]]:
    """构造只保留 `on_message` / `_send` 依赖的最小 handler。"""
    sent: list[str] = []
    closed: list[tuple] = []

    handler = object.__new__(wsController.EventsWsHandler)

    async def _send(payload: str) -> None:
        sent.append(payload)

    handler._send = _send  # type: ignore[method-assign]
    handler.close = lambda code=None, reason=None: closed.append((code, reason))  # type: ignore[method-assign]
    return handler, sent, closed


def _auth_config(enabled: bool, token: str = "secret") -> SimpleNamespace:
    return SimpleNamespace(setting=SimpleNamespace(auth=SimpleNamespace(enabled=enabled, token=token)))


@pytest.fixture
def subscribed(monkeypatch):
    """把 `_subscribe_events` 换成打点，避免测试真的挂到全局总线订阅表上。"""
    calls: list[int] = []
    monkeypatch.setattr(
        wsController.EventsWsHandler,
        "_subscribe_events",
        lambda self: calls.append(1),
    )
    return calls


async def test_auth_success_sends_auth_ok_before_subscribing(monkeypatch, subscribed):
    """认证通过：必须先发 `auth_ok`，再订阅事件。

    顺序有意义——如果先订阅再 ack，订阅期间产生的事件会先于 ack 抵达前端，
    前端仍会靠「首条消息」兜住，但 ack 的语义就变成了「可能晚于业务事件」，
    这条测试把契约钉死在「ack 先于订阅」。
    """
    monkeypatch.setattr(
        wsController.configUtil, "get_app_config", lambda: _auth_config(True, "secret")
    )
    handler, sent, closed = _fake_handler()

    handler.on_message(json.dumps({"type": "auth", "token": "secret"}))
    await asyncio.sleep(0)

    assert closed == [], "认证成功不应关闭连接"
    assert len(sent) == 1
    assert json.loads(sent[0]) == {"event": "auth_ok"}
    assert subscribed == [1]


async def test_auth_ok_is_plain_event_envelope(monkeypatch, subscribed):
    """`auth_ok` 帧形状：只有 `event` 键。

    不塞业务字段是刻意的——前端 `eventNormalizer` 对未知事件返回 null、
    不进业务分发，ack 只承担「连接就绪」这一个职责。
    """
    monkeypatch.setattr(
        wsController.configUtil, "get_app_config", lambda: _auth_config(True, "secret")
    )
    handler, sent, _ = _fake_handler()

    handler.on_message(json.dumps({"type": "auth", "token": "secret"}))
    await asyncio.sleep(0)

    decoded = json.loads(sent[0])
    assert list(decoded.keys()) == ["event"]
    assert decoded["event"] == "auth_ok"


async def test_wrong_token_sends_no_ack(monkeypatch, subscribed):
    """令牌错误：不发 ack、不订阅，直接以策略违规码关闭。

    这条防的是「ack 被挪到校验之前」——那样任何 token 都能拿到「已连接」，
    前端会显示连接正常但收不到任何推送。
    """
    monkeypatch.setattr(
        wsController.configUtil, "get_app_config", lambda: _auth_config(True, "secret")
    )
    handler, sent, closed = _fake_handler()

    handler.on_message(json.dumps({"type": "auth", "token": "wrong"}))
    await asyncio.sleep(0)

    assert sent == []
    assert closed == [(1008, "Invalid token")]
    assert subscribed == []


async def test_auth_disabled_sends_no_ack(monkeypatch, subscribed):
    """鉴权关闭：走 open 直接订阅的老路径，不发 ack（前端此时也不需要）。"""
    monkeypatch.setattr(
        wsController.configUtil, "get_app_config", lambda: _auth_config(False)
    )
    handler, sent, closed = _fake_handler()

    handler.on_message(json.dumps({"anything": 1}))
    await asyncio.sleep(0)

    assert sent == []
    assert closed == []
    assert subscribed == [1]
