"""WebSocket `agent_context_usage` 推送契约守卫。

前端进度条只认这条广播（`api.ts` 明确「响应的 context_usage 不写本地缓存，只认广播」），
所以两个契约破了都是**静默失效**——面板不更新、也没有任何报错：

- topic 必须在 `_WS_TOPICS` 里。漏订阅时后端一切正常、`messageBus.publish` 也照常
  返回成功，只是没人订阅 → 前端永远收不到。这条必须有断言，因为
  「只有发布侧断言」的测试在删掉订阅后依然全绿（实测 1578 passed）。
- 事件名必须是 `agent_context_usage`，且 `team_id` / `agent_id` / `context_usage`
  原样透出（前端按 agent_id 做身份校验、按 context_usage 渲染）。

与 `test_ws_memory_event.py` 同一套路：不开端口，直接构造 handler 并直调 `_on_event`
抓实际下发帧，不 grep 源码字符串。
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


def _usage_payload() -> dict:
    return {
        "used_tokens": 1000,
        "limit_tokens": 114688,
        "compact_trigger_tokens": 97484,
        "context_window_tokens": 131072,
        "ratio": 1000 / 114688,
    }


async def test_context_usage_topic_is_subscribed():
    """订阅漏了比推送错更难查：前端表现是「一切正常但进度条不动」。

    负对照：把 `AGENT_CONTEXT_USAGE_CHANGED` 从 `wsController._WS_TOPICS` 删掉 ⇒ 本用例转红。
    只断言发布侧（`messageBus.publish` 收到该 topic）抓不到这个漏，因为
    publish 对「零订阅者」是静默成功的。
    """
    assert MessageBusTopic.AGENT_CONTEXT_USAGE_CHANGED in wsController._WS_TOPICS


async def test_on_event_maps_context_usage_topic_to_agent_context_usage():
    """topic → 事件名映射，payload 原样透出 team_id / agent_id / context_usage。"""
    handler, sent = _fake_handler()

    handler._on_event(
        SimpleNamespace(
            topic=MessageBusTopic.AGENT_CONTEXT_USAGE_CHANGED,
            payload={
                "team_id": 2,
                "agent_id": 7,
                "context_usage": _usage_payload(),
            },
        )
    )
    await asyncio.sleep(0)

    assert len(sent) == 1
    decoded = json.loads(sent[0])
    assert decoded["event"] == "agent_context_usage"
    assert decoded["team_id"] == 2
    assert decoded["agent_id"] == 7
    # 进度条的每个字段都要能到前端：少一个前端就渲染成 undefined
    usage = decoded["context_usage"]
    assert usage["used_tokens"] == 1000
    assert usage["limit_tokens"] == 114688
    assert usage["compact_trigger_tokens"] == 97484
    assert usage["context_window_tokens"] == 131072
    assert usage["ratio"] == 1000 / 114688


async def test_on_event_does_not_tag_other_topics_as_context_usage():
    """反向断言：其它 topic 不得被误标成 `agent_context_usage`。

    一串 `if` 里漏写 `elif` / 复制粘贴改漏一个分支，就会让无关事件冒充用量推送，
    前端拿着错的 payload 去更新进度条。这里挑一个相邻 topic 做负对照。
    """
    handler, sent = _fake_handler()

    handler._on_event(
        SimpleNamespace(
            topic=MessageBusTopic.AGENT_MEMORY_CHANGED,
            payload={"team_id": 2, "agent_id": 7, "memory_id": 31, "action": "created"},
        )
    )
    await asyncio.sleep(0)

    assert len(sent) == 1
    assert json.loads(sent[0])["event"] == "agent_memory_changed"
