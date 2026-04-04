import asyncio
import logging
import tornado.websocket
import service.messageBus as messageBus
from constants import MessageBusTopic
from util import jsonUtil

logger = logging.getLogger(__name__)

_WS_TOPICS = [
    MessageBusTopic.ROOM_MSG_ADDED,
    MessageBusTopic.AGENT_STATUS_CHANGED,
]


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        logger.info("[ws] WebSocket opened")
        for topic in _WS_TOPICS:
            messageBus.subscribe(topic, self._on_event)

    def on_close(self):
        logger.info("[ws] WebSocket closed")
        for topic in _WS_TOPICS:
            messageBus.unsubscribe(topic, self._on_event)

    def on_message(self, message):
        pass  # 只推不收，忽略客户端消息

    def _on_event(self, msg: messageBus.EventBusMessage) -> None:
        payload = dict(msg.payload)
        if msg.topic == MessageBusTopic.AGENT_STATUS_CHANGED:
            payload["event"] = "agent_status"
        logger.info(f"[ws] event: topic={msg.topic.name}, payload={payload}")
        asyncio.get_event_loop().create_task(self._send(jsonUtil.json_dump(payload)))

    async def _send(self, payload: str) -> None:
        try:
            logger.debug(f"[ws] sending: {payload[:100]}...")
            self.write_message(payload)
            logger.debug(f"[ws] sent successfully")
        except tornado.websocket.WebSocketClosedError:
            logger.info("[ws] WebSocket closed, skipping message")
        except Exception as e:
            logger.error(f"[ws] error sending message: {e}")
