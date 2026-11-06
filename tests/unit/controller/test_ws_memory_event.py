"""WebSocket `agent_memory_changed` 推送契约（need_fix_1.md P1-3 a 段）。

前端 `AgentMemoryPanel` 靠这条事件重拉记忆列表，因此两个契约不能破：
- topic 必须在 `_WS_TOPICS` 里（漏订阅 = 前端永远收不到，且没有任何报错）；
- 事件名必须是 `agent_memory_changed`，且 `agent_id` 透出（面板按 agent 过滤）。

与 `test_ws_container_event.py` 同一套路：不开端口，直接构造 handler。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from constants import MessageBusTopic
from controller import wsController


def _fake_handler() -> tuple[wsController.EventsWsHandler, list[str]]:
    sent: list[str] = []
    handler = object.__new__(wsController.EventsWsHandler)

    async def _send(payload: str) -> None:
        sent.append(payload)

    handler._send = _send  # type: ignore[method-assign]
    return handler, sent


async def test_memory_changed_topic_is_subscribed():
    """订阅漏了比推送错更难查：前端表现是「一切正常但不刷新」。"""
    assert MessageBusTopic.AGENT_MEMORY_CHANGED in wsController._WS_TOPICS


async def test_on_event_maps_memory_topic_to_agent_memory_changed():
    """topic → 事件名映射，payload 原样透出 team_id / agent_id / memory_id / action。"""
    handler, sent = _fake_handler()

    handler._on_event(
        SimpleNamespace(
            topic=MessageBusTopic.AGENT_MEMORY_CHANGED,
            payload={"team_id": 2, "agent_id": 7, "memory_id": 31, "action": "created"},
        )
    )
    await asyncio.sleep(0)

    assert len(sent) == 1
    decoded = json.loads(sent[0])
    assert decoded["event"] == "agent_memory_changed"
    assert decoded["team_id"] == 2
    assert decoded["agent_id"] == 7
    assert decoded["memory_id"] == 31
    assert decoded["action"] == "created"
