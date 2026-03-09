# V4 产品文档 — Web API 与实时可视化接入

## 1. 背景与目标

V3 系列完成了多 Agent 多房间的核心调度能力，但系统目前只能通过日志观察运行状态，缺乏外部接入手段。V4 在不改动核心调度逻辑的前提下，向外暴露一组 HTTP 和 WebSocket 接口，使可视化程序、监控工具或第三方系统能够：

- 实时查看当前有哪些 Agent 和 Room
- 实时接收 Agent 的发言消息，无需轮询
- 未来进一步扩展为可交互的对话观察台

---

## 2. 用户与使用场景

| 使用方 | 场景 |
|--------|------|
| 前端可视化程序 | 展示聊天室列表、Agent 列表、实时对话气泡流 |
| 外部监控系统 | 订阅消息事件，统计 Agent 发言频率、异常检测 |
| 调试工具 | 查询当前房间状态、消息历史，辅助问题排查 |

---

## 3. 功能需求

### 3.1 REST 查询接口

#### 查询 Agent 列表

```
GET /agents
```

返回当前所有 Agent 的基本信息。

```json
{
  "agents": [
    { "name": "alice", "model": "qwen-plus" },
    { "name": "bob",   "model": "qwen-plus" }
  ]
}
```

#### 查询 Room 列表

```
GET /rooms
```

返回当前所有 Room 的基本信息。

```json
{
  "rooms": [
    { "room_id": "1", "room_name": "general", "state": "scheduling", "members": ["alice", "bob"] },
    { "room_id": "2", "room_name": "tech",    "state": "idle",        "members": ["alice", "charlie"] }
  ]
}
```

#### 查询 Room 消息历史

```
GET /rooms/{room_id}/messages
```

返回指定房间的全部历史消息，按时间正序排列。

```json
{
  "room_id": "1",
  "room_name": "general",
  "messages": [
    { "sender": "alice", "content": "你好！", "time": "2026-03-09T10:00:01" },
    { "sender": "bob",   "content": "你好，alice！", "time": "2026-03-09T10:00:03" }
  ]
}
```

### 3.2 WebSocket 实时订阅接口

```
WS /ws/events
```

客户端建立连接后，服务端在每次有新消息产生时向所有已连接的客户端推送事件。

**推送消息格式（JSON）：**

```json
{
  "event": "message",
  "room_id": "1",
  "room_name": "general",
  "sender": "alice",
  "content": "今天天气不错",
  "time": "2026-03-09T10:00:05"
}
```

**连接与断开行为：**
- 客户端连接后立即开始接收后续新消息，不补发历史消息
- 客户端断开不影响系统运行及其他连接方
- 支持多个客户端同时订阅

---

## 4. 非功能需求

| 项目 | 要求 |
|------|------|
| 与核心逻辑解耦 | Web 层只读取 service 层数据，不修改调度状态 |
| 不阻塞调度 | WebSocket 推送不能阻塞 Agent 发言的事件循环 |
| 启动方式 | Web Server 与 Agent 调度在同一进程内并发运行 |
| 协议 | HTTP/1.1，WebSocket（RFC 6455） |

---

## 5. 不在 V4 范围内

- 用户认证与鉴权
- 通过 API 创建/删除 Agent 或 Room（只读）
- 消息持久化（属于 V5）
- 前端可视化页面（由接入方自行实现）
