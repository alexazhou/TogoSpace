import asyncio
import json
import logging
import tornado.websocket
import service.messageBus as messageBus
from constants import MessageBusTopic
from controller.baseController import allowed_web_origins
from util import jsonUtil, configUtil

logger = logging.getLogger(__name__)

_WS_TOPICS = [
    MessageBusTopic.ROOM_MSG_ADDED,
    MessageBusTopic.ROOM_MSG_CHANGED,
    MessageBusTopic.ROOM_STATUS_CHANGED,
    MessageBusTopic.ROOM_ADDED,
    MessageBusTopic.AGENT_STATUS_CHANGED,
    MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED,
    MessageBusTopic.AGENT_ACTIVITY_CHANGED,
    MessageBusTopic.AGENT_MEMORY_CHANGED,
    MessageBusTopic.SCHEDULE_STATE_CHANGED,
    MessageBusTopic.TEAM_RELOADED,
    MessageBusTopic.TASK_CREATED,
    MessageBusTopic.TASK_CHANGED,
    MessageBusTopic.TASK_EVENT_APPENDED,
]


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin: str) -> bool:
        """放行桌面外壳与开发模式的页面源，其余沿用 Tornado 默认的同源判定。

        Tornado 默认拒绝跨源 WS；桌面窗口的 `tauri://localhost` 与后端
        `http://127.0.0.1:<port>` 必然跨源，必须显式加入白名单。
        """
        if origin in allowed_web_origins():
            return True
        return super().check_origin(origin)

    def open(self):
        auth_config = configUtil.get_app_config().setting.auth
        if auth_config.enabled:
            logger.info("[ws] WebSocket opened, waiting for auth")
            return

        logger.info("[ws] WebSocket opened, auth disabled, subscribing events")
        self._subscribe_events()

    def on_close(self):
        logger.info("[ws] WebSocket closed")
        messageBus.unsubscribe_many(_WS_TOPICS, self._on_event)

    def on_message(self, message):
        """处理客户端消息（认证消息）。"""
        auth_config = configUtil.get_app_config().setting.auth

        # 鉴权未启用，直接订阅
        if not auth_config.enabled:
            self._subscribe_events()
            return

        # 解析消息
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("[ws] Invalid message format")
            self.close(code=1008, reason="Invalid message")
            return

        # 检查是否为认证消息
        if data.get("type") != "auth":
            logger.warning("[ws] Expected auth message first")
            self.close(code=1008, reason="Auth required")
            return

        # 验证 token
        token = data.get("token", "")
        if token != auth_config.token:
            logger.warning("[ws] Auth failed: wrong token")
            self.close(code=1008, reason="Invalid token")
            return

        # 认证成功：先回一帧 ack，再订阅事件
        logger.info("[ws] Auth succeeded, subscribing events")
        self._send_auth_ack()
        self._subscribe_events()

    def _send_auth_ack(self) -> None:
        """回一帧认证确认，前端据此把连接状态从「连接中」置为已连接。

        为什么必须有这一帧：认证成功后原来只订阅事件、不回任何东西，但前端把
        「收到首条消息」当作连接就绪的信号（`wsClient.ts` 的 message 监听）。
        总线上没有事件时前端就一直显示「连接中」，要等到某条房间消息恰好推送
        才变绿——鉴权开启时的稳定复现路径。

        兼容性：旧版前端把「首条任意消息」当成功信号，收到 auth_ok 同样置绿，
        所以新增这一帧对旧前端无害。
        """
        asyncio.create_task(self._send(jsonUtil.json_dump({"event": "auth_ok"})))

    def _subscribe_events(self):
        """订阅消息总线事件。"""
        messageBus.subscribe_many(_WS_TOPICS, self._on_event)

    def _on_event(self, msg: messageBus.EventBusMessage) -> None:
        payload = dict(msg.payload)
        if msg.topic == MessageBusTopic.ROOM_MSG_ADDED:
            payload["event"] = "message"
        if msg.topic == MessageBusTopic.ROOM_MSG_CHANGED:
            payload["event"] = "message_changed"
        if msg.topic == MessageBusTopic.ROOM_STATUS_CHANGED:
            payload["event"] = "room_status"
        if msg.topic == MessageBusTopic.ROOM_ADDED:
            payload["event"] = "room_added"
        if msg.topic == MessageBusTopic.AGENT_STATUS_CHANGED:
            payload["event"] = "agent_status"
        if msg.topic == MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED:
            # 与前端 `agent_container` 对齐：payload 直接带 gt_agent / exec_env /
            # current_container_id，前端复用与 agent_status 同一套身份校验。
            payload["event"] = "agent_container"
        if msg.topic == MessageBusTopic.AGENT_ACTIVITY_CHANGED:
            payload["event"] = "agent_activity"
        if msg.topic == MessageBusTopic.AGENT_MEMORY_CHANGED:
            payload["event"] = "agent_memory_changed"
        if msg.topic == MessageBusTopic.SCHEDULE_STATE_CHANGED:
            payload["event"] = "schedule_state"
        if msg.topic == MessageBusTopic.TEAM_RELOADED:
            payload["event"] = "team_reloaded"
        if msg.topic == MessageBusTopic.TASK_CREATED:
            payload["event"] = "task_created"
        if msg.topic == MessageBusTopic.TASK_CHANGED:
            payload["event"] = "task_changed"
        if msg.topic == MessageBusTopic.TASK_EVENT_APPENDED:
            # 业务事件在 payload 里叫 `task_event`（publish 时已这样命名），
            # 与这里的「消息类型」`event` 错开，避免互相覆盖。
            payload["event"] = "task_event_appended"
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
