from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from constants import MessageBusTopic

logger = logging.getLogger(__name__)


@dataclass
class Message:
    topic: MessageBusTopic
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: int = 0


_subscribers: Dict[MessageBusTopic, List[Callable[[Message], None]]] = {}
_event_id_counter: int = 0


def _next_event_id() -> int:
    global _event_id_counter
    _event_id_counter += 1
    return _event_id_counter


def subscribe(topic: MessageBusTopic, callback: Callable[[Message], None]) -> None:
    """订阅指定主题，callback 接收 Message 对象。"""
    _subscribers.setdefault(topic, []).append(callback)


def unsubscribe(topic: MessageBusTopic, callback: Callable[[Message], None]) -> None:
    """取消订阅指定主题。"""
    callbacks: List[Callable[[Message], None]] = _subscribers.get(topic, [])
    if callback in callbacks:
        callbacks.remove(callback)


def publish(topic: MessageBusTopic, **payload: Any) -> None:
    """向指定主题的所有订阅者投递消息。

    回调统一在当前运行中的 asyncio 事件循环里异步调度，避免慢订阅者阻塞发布链路。
    """
    msg = Message(event_id=_next_event_id(), topic=topic, payload=payload)
    logger.info(f"[messageBus] publish event_id={msg.event_id} topic={topic.name}, payload={payload}")
    callbacks = list(_subscribers.get(topic, []))
    loop = asyncio.get_running_loop()

    for cb in callbacks:
        loop.call_soon(_invoke_callback, cb, msg)


def _invoke_callback(callback: Callable[[Message], None], msg: Message) -> None:
    callback_name = getattr(callback, "__name__", repr(callback))
    try:
        result = callback(msg)
        if inspect.isawaitable(result):
            asyncio.create_task(result, name=f"mb-{msg.event_id}-{callback_name}")
    except Exception as e:
        logger.error(f"[messageBus] event_id={msg.event_id} topic={msg.topic} callback={callback_name} 异常: {e}")


async def startup() -> None:
    """初始化消息总线，须在各模块 subscribe 前调用。"""
    global _event_id_counter
    _event_id_counter = 0
    _subscribers.clear()


async def shutdown() -> None:
    """清空所有订阅，程序退出前调用。"""
    _subscribers.clear()