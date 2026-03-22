import asyncio
import json
import tornado.websocket
import service.messageBus as messageBus
from model.coreModel.gtCoreWebModel import WsEvent
from constants import MessageBusTopic


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        messageBus.subscribe(MessageBusTopic.ROOM_MSG_ADDED, self._on_message_added)
        messageBus.subscribe(MessageBusTopic.AGENT_STATUS_CHANGED, self._on_agent_status_changed)

    def on_close(self):
        messageBus.unsubscribe(MessageBusTopic.ROOM_MSG_ADDED, self._on_message_added)
        messageBus.unsubscribe(MessageBusTopic.AGENT_STATUS_CHANGED, self._on_agent_status_changed)

    def on_message(self, message):
        pass  # 只推不收，忽略客户端消息

    def _on_message_added(self, msg) -> None:
        event = WsEvent(
            event="message",
            room_id=msg.payload.get("room_id"),
            room_name=msg.payload["room_name"],
            team_name=msg.payload.get("team_name"),
            sender=msg.payload["sender"],
            content=msg.payload["content"],
            time=msg.payload["time"],
        )
        asyncio.get_event_loop().create_task(
            self._send(json.dumps(event.model_dump(mode="json"), ensure_ascii=False))
        )

    def _on_agent_status_changed(self, msg) -> None:
        payload = {
            "event": "agent_status",
            "agent_name": msg.payload["agent_name"],
            "team_name": msg.payload["team_name"],
            "status": msg.payload["status"],
        }
        asyncio.get_event_loop().create_task(self._send(json.dumps(payload, ensure_ascii=False)))

    async def _send(self, payload: str) -> None:
        try:
            self.write_message(payload)
        except tornado.websocket.WebSocketClosedError:
            pass
