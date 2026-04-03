import asyncio
import json
import logging
import tornado.websocket
import service.messageBus as messageBus
from model.coreModel.gtCoreWebModel import GtCoreWsEvent
from constants import MessageBusTopic

logger = logging.getLogger(__name__)


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        logger.info("[ws] WebSocket opened")
        messageBus.subscribe(MessageBusTopic.ROOM_MSG_ADDED, self._on_message_added)
        messageBus.subscribe(MessageBusTopic.AGENT_STATUS_CHANGED, self._on_agent_status_changed)

    def on_close(self):
        logger.info("[ws] WebSocket closed")
        messageBus.unsubscribe(MessageBusTopic.ROOM_MSG_ADDED, self._on_message_added)
        messageBus.unsubscribe(MessageBusTopic.AGENT_STATUS_CHANGED, self._on_agent_status_changed)

    def on_message(self, message):
        pass  # 只推不收，忽略客户端消息

    def _on_message_added(self, msg) -> None:
        event = GtCoreWsEvent(
            event="message",
            room_id=msg.payload.get("room_id"),
            room_key=msg.payload.get("room_key"),
            room_name=msg.payload["room_name"],
            team_id=msg.payload["team_id"],
            team_name=msg.payload.get("team_name"),
            sender=msg.payload["sender"],
            content=msg.payload["content"],
            time=msg.payload["time"],
        )
        asyncio.get_event_loop().create_task(
            self._send(json.dumps(event.model_dump(mode="json"), ensure_ascii=False))
        )

    def _on_agent_status_changed(self, msg) -> None:
        gt_agent = msg.payload["gt_agent"]
        payload = {
            "event": "agent_status",
            "agent_name": gt_agent.name,
            "agent_id": gt_agent.id,
            "team_id": gt_agent.team_id,
            "status": msg.payload["status"],
        }
        logger.info(f"[ws] agent_status_changed: {payload}")
        asyncio.get_event_loop().create_task(self._send(json.dumps(payload, ensure_ascii=False)))

    async def _send(self, payload: str) -> None:
        try:
            logger.debug(f"[ws] sending: {payload[:100]}...")
            self.write_message(payload)
            logger.debug(f"[ws] sent successfully")
        except tornado.websocket.WebSocketClosedError:
            logger.info("[ws] WebSocket closed, skipping message")
        except Exception as e:
            logger.error(f"[ws] error sending message: {e}")
