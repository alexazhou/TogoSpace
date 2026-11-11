import asyncio
import json
import logging
import tornado.websocket
import service.messageBus as messageBus
from constants import MessageBusTopic
from controller.baseController import allowed_web_origins
from util import jsonUtil, configUtil, frontendLog

logger = logging.getLogger(__name__)


def _parse_json_object(message) -> dict | None:
    """解析上行报文为 JSON 对象；非对象/非法 JSON 返回 None。

    统一成一个函数是为了让「鉴权关闭」与「已认证」两条分支的解析口径完全一致 ——
    两份 `json.loads` 各写一遍，早晚会有一份漏掉 `isinstance(data, dict)`，
    于是 `[1,2,3]` 这种报文会在 `.get()` 上炸掉整个 ws 回调。
    """
    try:
        data = json.loads(message)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _is_frontend_log(data: dict) -> bool:
    """是否为前端日志上报报文。"""
    return data.get("type") == "frontend_log"


_WS_TOPICS = [
    MessageBusTopic.ROOM_MSG_ADDED,
    MessageBusTopic.ROOM_MSG_CHANGED,
    MessageBusTopic.ROOM_STATUS_CHANGED,
    MessageBusTopic.ROOM_ADDED,
    MessageBusTopic.AGENT_STATUS_CHANGED,
    MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED,
    MessageBusTopic.AGENT_ACTIVITY_CHANGED,
    MessageBusTopic.AGENT_MEMORY_CHANGED,
    MessageBusTopic.AGENT_CONTEXT_USAGE_CHANGED,
    MessageBusTopic.SCHEDULE_STATE_CHANGED,
    MessageBusTopic.TEAM_RELOADED,
    MessageBusTopic.TASK_CREATED,
    MessageBusTopic.TASK_CHANGED,
    MessageBusTopic.TASK_EVENT_APPENDED,
]


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    #: 这条连接是否已通过鉴权。鉴权关闭时 `open()` 直接置 True（没有鉴权就等于已授权）。
    #: 用**类属性**给默认值：测试用 `object.__new__` 构造 handler，不走 `open()`，
    #: 实例属性不存在时读到这里的 False 而不是 AttributeError。
    _authenticated: bool = False

    #: 是否已订阅总线事件。`subscribe_many` 是**追加**语义，重复调用会让同一条事件
    #: 被推多次；原来只有「鉴权关闭时收到上行」才会重复订阅，那条路在修复前实际上
    #: 永远不会被走到（前端在鉴权关闭时不发任何上行），所以问题没暴露。本单让前端
    #: 持续发 `frontend_log`，必须把订阅做成幂等的，否则日志一发、事件就成倍推送。
    _events_subscribed: bool = False

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
        # 没有鉴权 ⇒ 这条连接一开就等价于已授权，前端可以立刻发日志。
        self._authenticated = True
        self._subscribe_events()

    def on_close(self):
        logger.info("[ws] WebSocket closed")
        messageBus.unsubscribe_many(_WS_TOPICS, self._on_event)
        self._events_subscribed = False
        self._authenticated = False

    def on_message(self, message):
        """处理客户端上行报文。

        支持两种，**按 `type` 分派**（原来只认 `auth`，鉴权关闭时甚至不解析内容）：

        - `{"type": "auth", "token": ...}`  —— 鉴权握手（既有行为，不变）
        - `{"type": "frontend_log", ...}`   —— 前端 console 日志（#197 新增）

        `frontend_log` 只在**已授权**连接上处理：鉴权开启时必须在 `auth` 成功之后；
        鉴权关闭时 `open()` 即视为已授权。未授权时收到的 `frontend_log` **静默丢弃
        且不关连接** —— 前端可能在握手完成前就把日志发出来，为一条日志断掉实时推送
        不划算；反过来说，把它当成「首帧必须是 auth」的违例去关连接，会让「日志量大
        时实时连接反复掉线」变成一个极难查的问题。
        """
        auth_config = configUtil.get_app_config().setting.auth

        # ── 鉴权未启用：连接即已授权 ──────────────────────────────────
        if not auth_config.enabled:
            self._ensure_subscribed()
            # 保持既有语义：这条分支**从不关闭连接**、也不回任何帧。
            # 解析失败同样只是忽略（原来压根不解析，不能因为新增分支变成会断连）。
            data = _parse_json_object(message)
            if data is not None:
                self._maybe_handle_frontend_log(data)
            return

        # ── 鉴权启用、且已认证：只接受日志上报 ────────────────────────
        if self._authenticated:
            data = _parse_json_object(message)
            if data is not None:
                self._maybe_handle_frontend_log(data)
            return

        # ── 鉴权启用、尚未认证 ────────────────────────────────────────
        data = _parse_json_object(message)
        if data is None:
            logger.warning("[ws] Invalid message format")
            self.close(code=1008, reason="Invalid message")
            return

        # 握手前的前端日志：丢弃，不断连接（见方法 docstring）。
        if _is_frontend_log(data):
            logger.debug("[ws] frontend_log before auth, dropped")
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
        self._authenticated = True
        self._send_auth_ack()
        self._subscribe_events()

    # ── 前端日志桥（#197）─────────────────────────────────────────────

    def _maybe_handle_frontend_log(self, data: dict) -> None:
        """把 `frontend_log` 报文交给 `frontendLog` 模块落盘；其它类型一律忽略。

        落盘条件（调试开关、级别白名单、单条截断）都在 `frontendLog` 内，
        控制器只管**授权**这一层 —— 两件事分开，才好各自测。

        **吞掉落盘异常**：这里位于 ws 的 `on_message` 回调中，异常的代价不是「少一条
        日志」，而是把这条连接的实时推送一起打断。磁盘满、目录权限这类故障必须降级成
        「日志写不进去」而不是「实时连接崩了」，但也不能静默 —— 记进 controller.log。
        """
        if not _is_frontend_log(data):
            return
        try:
            frontendLog.handle_message(data, client=self._client_label())
        except Exception:
            logger.warning("[ws] frontend_log 落盘失败，已忽略", exc_info=True)

    def _client_label(self) -> str:
        """连接标识：多窗口/多客户端时用来区分日志来源。"""
        request = getattr(self, "request", None)
        remote_ip = getattr(request, "remote_ip", None)
        if not remote_ip:
            return "-"
        return f"{remote_ip}:{getattr(request, 'remote_port', '-') or '-'}"

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
        """订阅消息总线事件（幂等）。"""
        messageBus.subscribe_many(_WS_TOPICS, self._on_event)
        self._events_subscribed = True

    def _ensure_subscribed(self):
        """订阅事件，已订阅则跳过。

        `subscribe_many` 是**追加**语义；不幂等的话，前端每发一条日志就多挂一个
        回调，业务事件会被成倍推送。本单让前端持续发上行，这个坑必须先堵上。
        """
        if self._events_subscribed:
            return
        self._subscribe_events()

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
        if msg.topic == MessageBusTopic.AGENT_CONTEXT_USAGE_CHANGED:
            # payload 直接带 team_id / agent_id / context_usage，前端走与 agent_status
            # 同一套身份校验（agent_id 非数或 <=0 判脏丢弃）。
            payload["event"] = "agent_context_usage"
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
