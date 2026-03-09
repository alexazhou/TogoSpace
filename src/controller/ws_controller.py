import asyncio
import json
import tornado.websocket
import service.message_bus as message_bus
from model.web_model import WsEvent
from constants import MessageBusTopic

# 模块级连接池，所有 handler 实例共享
_clients: set["EventsWsHandler"] = set()


def _on_message_added(msg) -> None:
    """message_bus 同步回调，将广播任务投入事件循环。"""
    event = WsEvent(
        event="message",
        room_id=msg.payload["room_name"],
        room_name=msg.payload["room_name"],
        sender=msg.payload["sender"],
        content=msg.payload["content"],
        time=msg.payload["time"],
    )
    asyncio.get_event_loop().create_task(_broadcast(json.dumps(event.model_dump(mode="json"), ensure_ascii=False)))


async def _broadcast(payload: str) -> None:
    global _clients
    dead = set()
    for client in _clients:
        try:
            client.write_message(payload)
        except tornado.websocket.WebSocketClosedError:
            dead.add(client)
    _clients -= dead


def init() -> None:
    """订阅消息总线，须在服务启动前调用一次。"""
    message_bus.subscribe(MessageBusTopic.ROOM_MSG_ADDED, _on_message_added)


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        _clients.add(self)

    def on_close(self):
        _clients.discard(self)

    def on_message(self, message):
        pass  # 只推不收，忽略客户端消息
