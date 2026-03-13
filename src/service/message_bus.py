import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from constants import MessageBusTopic

logger = logging.getLogger(__name__)


@dataclass
class Message:
    topic: str
    payload: Dict[str, Any] = field(default_factory=dict)


_subscribers: Dict[str, List[Callable[[Message], None]]] = {}


def subscribe(topic: str, callback: Callable[[Message], None]) -> None:
    """订阅指定主题，callback 接收 Message 对象。"""
    _subscribers.setdefault(topic, []).append(callback)


def unsubscribe(topic: str, callback: Callable[[Message], None]) -> None:
    """取消订阅指定主题。"""
    callbacks = _subscribers.get(topic, [])
    if callback in callbacks:
        callbacks.remove(callback)


def publish(topic: str, **payload: Any) -> None:
    """向指定主题的所有订阅者投递消息。"""
    msg = Message(topic=topic, payload=payload)
    for cb in _subscribers.get(topic, []):
        try:
            cb(msg)
        except Exception as e:
            logger.error(f"[message_bus] topic={topic} callback={cb.__name__} 异常: {e}")


def startup() -> None:
    """初始化消息总线，须在各模块 subscribe 前调用。"""
    _subscribers.clear()


def shutdown() -> None:
    """清空所有订阅，程序退出前调用。"""
    _subscribers.clear()
