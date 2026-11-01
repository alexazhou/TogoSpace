"""第 7 步：WebSocket 事件 `agent_container` 的推送契约（设计文档 §8）。

前端 `eventNormalizer` 对这条事件的处理有两条硬要求，这里一并钉住：
- payload 里必须带**完整** `gt_agent`（前端 `readGtAgentIdentity` 校验身份，缺字段
  整条事件会被丢弃）；
- 事件名必须是 `agent_container`（不是 topic 名、也不是 `agent_status`）。

不启动真实 tornado 服务：`_on_event` 只依赖 `msg` + `_send`，直接构造 handler
实例并把 `_send` 换成收集器即可，比开监听端口稳定。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from constants import MessageBusTopic
from controller import wsController


def _fake_handler() -> tuple[wsController.EventsWsHandler, list[str]]:
    """构造只保留 `_on_event` 依赖的最小 handler。"""
    sent: list[str] = []

    handler = object.__new__(wsController.EventsWsHandler)

    async def _send(payload: str) -> None:
        sent.append(payload)

    handler._send = _send  # type: ignore[method-assign]
    return handler, sent


def _message(topic: MessageBusTopic, **payload) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=payload)


async def test_agent_container_topic_is_subscribed():
    """topic 必须在 `_WS_TOPICS` 里，否则前端永远收不到（订阅漏了比推送错更难查）。"""
    assert MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED in wsController._WS_TOPICS


async def test_on_event_maps_container_topic_to_agent_container_event():
    """topic → 事件名映射：`agent_container`，payload 原样透出三个字段。"""
    handler, sent = _fake_handler()
    gt_agent = SimpleNamespace(id=3, team_id=1)

    handler._on_event(
        _message(
            MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED,
            gt_agent=gt_agent,
            exec_env="container",
            current_container_id="c0ffee123456",
        )
    )
    # `_send` 由 create_task 调度，让出一次事件循环
    await asyncio.sleep(0)

    assert len(sent) == 1
    decoded = json.loads(sent[0])
    assert decoded["event"] == "agent_container"
    assert decoded["exec_env"] == "container"
    assert decoded["current_container_id"] == "c0ffee123456"
    # 完整 gt_agent（前端做身份校验用）
    assert decoded["gt_agent"]["id"] == 3
    assert decoded["gt_agent"]["team_id"] == 1


async def test_on_event_keeps_agent_status_and_container_events_distinct():
    """两条事件不能互相覆盖：`agent_status` 只改 status，`agent_container` 只改环境。

    若映射写错（例如两者都用 `agent_status`），前端会把环境变更当成忙闲变更，
    容器徽标就不会更新——这种错在 UI 上很难定位。
    """
    handler, sent = _fake_handler()

    handler._on_event(
        _message(MessageBusTopic.AGENT_STATUS_CHANGED, gt_agent=SimpleNamespace(id=1, team_id=1), status="BUSY")
    )
    handler._on_event(
        _message(
            MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED,
            gt_agent=SimpleNamespace(id=1, team_id=1),
            exec_env="host",
            current_container_id=None,
        )
    )
    await asyncio.sleep(0)

    events = [json.loads(item)["event"] for item in sent]
    assert events == ["agent_status", "agent_container"]
    assert json.loads(sent[1])["current_container_id"] is None
