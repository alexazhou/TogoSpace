# V4: Web API 与实时可视化接入 - 技术文档

---

## 架构设计

### V3.x vs V4 架构对比

V3.x 系列的核心调度逻辑完全封闭在进程内，外部无法观察运行状态。V4 新增 **controller 层**和 **route.py**，以 Tornado 作为 HTTP/WebSocket 服务器，与 Agent 调度在**同一个 asyncio 事件循环**中并发运行，不阻塞调度、不修改任何现有 service。

### 分层职责

| 层 | 模块 | 职责 |
|----|------|------|
| controller | `controller/base_controller.py` | `BaseHandler` 基类，统一 Pydantic 序列化 |
| controller | `controller/agentController.py` | 处理 `GET /agents` 请求，调用 service 组装响应 |
| controller | `controller/roomController.py` | 处理 `GET /rooms`、`GET /rooms/{room_id}/messages` |
| controller | `controller/wsController.py` | 管理 WebSocket 连接，订阅消息总线并广播事件 |
| route | `route.py` | 声明 URL → Handler 路由表，供 `main.py` 挂载 |
| model | `model/coreModel/gtCoreWebModel.py` | HTTP/WS 响应的 Pydantic 数据模型 |

controller 层只负责**请求解析与响应序列化**，业务数据全部来自 service 层，不直接操作底层数据结构。

### 架构图

```
  外部客户端（可视化/监控）
        ▲ HTTP / WebSocket
        │
┌───────┴──────────────────────────────────────────────────┐
│                     asyncio 事件循环                       │
│                                                          │
│  ┌─────────────────┐       ┌────────────────────────┐    │
│  │ schedulerService│       │  Tornado HTTPServer    │    │
│  │  (Agent 调度)    │       │  (main.py 启动)        │    │
│  └────────┬────────┘       └──────────┬─────────────┘    │
│           │ 读写                       │ route.py 路由表   │
│           │              ┌────────────┼──────────────┐    │
│           │              ▼            ▼              ▼    │
│           │    ┌──────────────┐ ┌──────────────┐ ┌─────┐ │
│           │    │agent_ctrl    │ │room_ctrl      │ │ws   │ │
│           │    │              │ │               │ │ctrl │ │
│           │    └──────┬───────┘ └──────┬────────┘ └──┬──┘ │
│           │           │ 只读           │ 只读         │订阅 │
│  ┌────────▼───────────▼───────────────▼──────────────▼──┐ │
│  │                   service 层（现有，不修改）             │ │
│  │      agentService    roomService    messageBus     │ │
│  └───────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### 并发模型

`main.py` 直接挂载路由、启动 Tornado 服务器，并用 `asyncio.gather` 与调度循环并发运行：

```python
import asyncio
import tornado.httpserver
from route import make_app
import service.schedulerService as scheduler
from controller.wsController import init as init_ws

async def main():
    init_ws()                              # 订阅消息总线
    server = tornado.httpserver.HTTPServer(make_app())
    server.listen(8080, "0.0.0.0")
    await asyncio.gather(
        scheduler.run(),
        asyncio.Event().wait(),            # 保持事件循环存活
    )

asyncio.run(main())
```

Tornado 原生支持 asyncio 事件循环，HTTP 请求和 WebSocket 推送均为协程，不阻塞 Agent 的 LLM 调用。

---

## Room ID 设计

产品文档中 `room_id` 与 `room_name` 是独立字段。当前 `roomService` 以 `name`（字符串）作为唯一标识。

V4 采用最小改动方案：**以 `room_name` 的值同时作为 `room_id`**，即 `room_id == room_name`。

HTTP 响应中两个字段均返回，保持与产品文档结构一致。后续如需引入自增 ID 或 UUID，只需修改 `web_model.py` 和 controller，不涉及 service 层。

---

## 数据模型（model/coreModel/gtCoreWebModel.py）

```python
from pydantic import BaseModel
from typing import List
from datetime import datetime


class AgentInfo(BaseModel):
    name: str
    model: str


class RoomInfo(BaseModel):
    room_id: str       # 当前等于 room_name
    room_name: str
    state: str         # "scheduling" | "idle"
    members: List[str]


class MessageInfo(BaseModel):
    sender: str
    content: str
    time: datetime


class RoomMessagesResponse(BaseModel):
    room_id: str
    room_name: str
    messages: List[MessageInfo]


class WsEvent(BaseModel):
    event: str         # 固定为 "message"
    room_id: str
    room_name: str
    sender: str
    content: str
    time: datetime
```

---

## Controller 层

Tornado 的 HTTP handler 继承 `tornado.web.RequestHandler`，WebSocket handler 继承 `tornado.websocket.WebSocketHandler`。

所有 HTTP handler 继承 `BaseHandler`，由基类统一处理 Pydantic 序列化与响应头设置。

### controller/base_controller.py

```python
import json
import tornado.web
from pydantic import BaseModel


class BaseHandler(tornado.web.RequestHandler):
    """所有 HTTP controller 的基类，提供统一的 JSON 响应方法。"""

    def return_json(self, data) -> None:
        """序列化并写入 JSON 响应。

        - Pydantic BaseModel：调用 model_dump(mode="json") 处理 datetime 等类型
        - dict / list：直接 json.dumps
        """
        self.set_header("Content-Type", "application/json")
        if isinstance(data, BaseModel):
            self.write(data.model_dump(mode="json"))
        else:
            self.write(json.dumps(data, ensure_ascii=False))
```

> **为什么需要 `mode="json"`**：Tornado 的 `self.write(dict)` 内部使用 Python 标准 `json` 模块，无法序列化 `datetime` 对象。`model_dump(mode="json")` 会将所有字段递归转换为 JSON 兼容类型（`datetime` → ISO 8601 字符串），再交给 Tornado 写出。

### controller/agentController.py

```python
import service.agentService as agentService
from model.coreModel.gtCoreWebModel import AgentInfo
from controller.baseController import BaseHandler


class AgentListHandler(BaseHandler):
    async def get(self):
        agents = agentService.get_all_agents()
        data = [AgentInfo(name=a.name, model=a.model).model_dump(mode="json") for a in agents]
        self.return_json({"agents": data})
```

### controller/roomController.py

```python
import service.roomService as roomService
from model.coreModel.gtCoreWebModel import RoomInfo, MessageInfo, RoomMessagesResponse
from controller.baseController import BaseHandler


class RoomListHandler(BaseHandler):
    async def get(self):
        rooms = roomService.get_all_rooms()
        data = [
            RoomInfo(
                room_id=r.name,
                room_name=r.name,
                state=r.state.value,
                members=r.member_names,
            ).model_dump(mode="json")
            for r in rooms
        ]
        self.return_json({"rooms": data})


class RoomMessagesHandler(BaseHandler):
    async def get(self, room_id: str):
        try:
            room = roomService.get_room(room_id)
        except RuntimeError:
            self.set_status(404)
            self.return_json({"error": f"room '{room_id}' not found"})
            return

        messages = [
            MessageInfo(sender=m.sender_name, content=m.content, time=m.send_time)
            for m in room.messages
        ]
        resp = RoomMessagesResponse(
            room_id=room.name, room_name=room.name, messages=messages
        )
        self.return_json(resp)
```

### controller/wsController.py

WebSocket handler 同时承担**连接管理**和**消息广播**职责：

```python
import asyncio
import tornado.websocket
import service.messageBus as messageBus
from model.coreModel.gtCoreWebModel import WsEvent
from constants import messageBusTopic

# 模块级连接池，所有 handler 实例共享
_clients: set["EventsWsHandler"] = set()


def _on_message_added(msg) -> None:
    """messageBus 同步回调，将广播任务投入事件循环。"""
    event = WsEvent(
        event="message",
        room_id=msg.payload["room_name"],
        room_name=msg.payload["room_name"],
        sender=msg.payload["sender"],
        content=msg.payload["content"],
        time=msg.payload["time"],
    )
    asyncio.get_event_loop().create_task(_broadcast(event.model_dump(mode="json")))


async def _broadcast(payload: dict) -> None:
    dead = set()
    for client in _clients:
        try:
            client.write_message(payload)
        except tornado.websocket.WebSocketClosedError:
            dead.add(client)
    _clients -= dead


def init() -> None:
    """订阅消息总线，须在服务启动前调用一次。"""
    messageBus.subscribe(messageBusTopic.ROOM_MSG_ADDED, _on_message_added)


class EventsWsHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        _clients.add(self)

    def on_close(self):
        _clients.discard(self)

    def on_message(self, message):
        pass  # 只推不收，忽略客户端消息
```

---

## route.py

`route.py` 只负责**声明 URL → Handler 映射**，不含启停逻辑，由 `main.py` 负责创建服务器并挂载：

```python
import tornado.web
from controller.agentController import AgentListHandler
from controller.roomController import RoomListHandler, RoomMessagesHandler
from controller.wsController import EventsWsHandler


def make_app() -> tornado.web.Application:
    return tornado.web.Application([
        (r"/agents",                 AgentListHandler),
        (r"/rooms",                  RoomListHandler),
        (r"/rooms/([^/]+)/messages", RoomMessagesHandler),
        (r"/ws/events",              EventsWsHandler),
    ])
```

---

## 新增 messageBus topic

`roomService.add_message` 写入消息后发布 `ROOM_MSG_ADDED` 事件，供 wsController 订阅：

```python
# constants.py
class messageBusTopic(str, Enum):
    ROOM_AGENT_TURN = "room.agent_turn"
    ROOM_MSG_ADDED  = "room.message_added"   # 新增

# roomService.py — ChatRoom.add_message 末尾追加
messageBus.publish(
    messageBusTopic.ROOM_MSG_ADDED,
    room_name=self.name,
    sender=sender,
    content=content,
    time=message.send_time.isoformat(),
)
```

---

## 目录结构

```
agent_team/
├── src/
│   ├── controller/                      # 新增：controller 层
│   │   ├── __init__.py
│   │   ├── base_controller.py           # BaseHandler，统一 JSON 响应与 Pydantic 序列化
│   │   ├── agentController.py          # GET /agents
│   │   ├── roomController.py           # GET /rooms, GET /rooms/{room_id}/messages
│   │   └── wsController.py             # WS /ws/events，连接管理与广播
│   ├── model/
│   │   ├── web_model.py                 # 新增：HTTP/WS 响应 Pydantic 模型
│   │   └── ...（现有，不变）
│   ├── service/
│   │   ├── roomService.py              # 修改：add_message 发布 ROOM_MSG_ADDED
│   │   └── ...（现有，不变）
│   ├── route.py                         # 新增：URL → Handler 路由表
│   ├── constants.py                     # 修改：新增 ROOM_MSG_ADDED topic
│   └── main.py                          # 修改：挂载 route.py，asyncio.gather 启动服务器
```

---

## 接口定义

### route.py（新增）

```python
def make_app() -> tornado.web.Application:
    """返回挂载所有路由的 Tornado Application，供 main.py 使用。"""
```

### roomService.py（新增辅助函数）

```python
def get_all_rooms() -> List[ChatRoom]:
    """返回所有 ChatRoom 实例列表。"""
```

---

## 关键设计决策

### controller 层只做请求/响应转换

controller 不持有任何状态，不包含业务判断，只负责从 URL/请求体中提取参数、调用 service、将结果序列化为 JSON。service 层的错误通过异常向上传播，controller 统一捕获后映射为 HTTP 状态码。

### WebSocket 广播不阻塞调度

`_on_message_added` 是 `messageBus` 的同步回调，内部通过 `asyncio.create_task` 将 `_broadcast` 投入事件循环异步执行，不在回调中阻塞等待发送完成。

### 客户端断开容错

`_broadcast` 捕获 `WebSocketClosedError`，遍历结束后批量从 `_clients` 中移除已断开的连接，避免僵尸连接积累。

---

## 修改的模块

| 模块 | 变更 | 依赖的项目内模块 |
|------|------|----------------|
| `model/coreModel/gtCoreWebModel.py` | 新增，HTTP/WS 响应 Pydantic 模型 | 无 |
| `controller/base_controller.py` | 新增，`BaseHandler` 基类，统一 Pydantic 序列化 | 无 |
| `controller/agentController.py` | 新增，处理 `/agents` | `service.agentService`、`model.coreModel.gtCoreWebModel`、`controller.baseController` |
| `controller/roomController.py` | 新增，处理 `/rooms`、`/rooms/{room_id}/messages` | `service.roomService`、`model.coreModel.gtCoreWebModel`、`controller.baseController` |
| `controller/wsController.py` | 新增，WebSocket 连接管理与广播 | `service.messageBus`、`model.coreModel.gtCoreWebModel`、`constants` |
| `route.py` | 新增，URL → Handler 路由表 | `controller.*` |
| `constants.py` | 新增 `ROOM_MSG_ADDED` topic | 无 |
| `service/roomService.py` | `add_message` 末尾发布 `ROOM_MSG_ADDED` | `service.messageBus` |
| `main.py` | 挂载 `route.make_app()`，`asyncio.gather` 启动服务器 | `route`、`controller.wsController` |
