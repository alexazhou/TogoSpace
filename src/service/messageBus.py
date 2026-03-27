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


_subscribers: Dict[MessageBusTopic, List[Callable[[Message], None]]] = {}
_pending_tasks: set[asyncio.Task] = set()


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
    msg = Message(topic=topic, payload=payload)
    logger.info(f"[messageBus] publish topic={topic.name}, payload={payload}")
    callbacks = list(_subscribers.get(topic, []))
    loop = asyncio.get_running_loop()

    for cb in callbacks:
        loop.call_soon(_invoke_callback, cb, msg)


def _invoke_callback(callback: Callable[[Message], None], msg: Message) -> None:
    callback_name = getattr(callback, "__name__", repr(callback))
    try:
        logger.debug(f"[messageBus] calling callback {callback_name} for topic={msg.topic.name}")
        result = callback(msg)
        if inspect.isawaitable(result):
            task = asyncio.create_task(_await_callback_result(result, msg.topic, callback_name))
            _pending_tasks.add(task)
            task.add_done_callback(_pending_tasks.discard)
    except Exception as e:
        logger.error(f"[messageBus] topic={msg.topic} callback={callback_name} 异常: {e}")


async def _await_callback_result(awaitable: Any, topic: MessageBusTopic, callback_name: str) -> None:
    try:
        await awaitable
    except Exception as e:
        logger.error(f"[messageBus] topic={topic} callback={callback_name} 异常: {e}")


async def startup() -> None:
    """初始化消息总线，须在各模块 subscribe 前调用。"""
    global _pending_tasks
    for task in list(_pending_tasks):
        if not task.done():
            task.cancel()
    _pending_tasks = set()
    _subscribers.clear()


def shutdown() -> None:
    """清空所有订阅，程序退出前调用。"""
    global _pending_tasks
    for task in list(_pending_tasks):
        if not task.done():
            task.cancel()
    _pending_tasks = set()
    _subscribers.clear()
