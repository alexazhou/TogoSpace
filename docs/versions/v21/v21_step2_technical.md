# V21: Agent 监督指令与控制房间 - 技术文档

## 1. 目标

在 V20 即时注入能力的基础上，提供两件事：

1. **按需建房**：为操作者和指定 Agent 之间自动建立一个专属私聊控制房间，首次使用时创建，后续复用，无需手动建立。
2. **活动视图指令入口**：在前端 Agent 活动面板中增加一个轻量输入区域，让操作者无需跳转到私聊房间，就能直接向 Agent 发送追加指令，底层通过控制房间传递。

---

## 2. 核心概念

### 控制房间（Control Room）

与某 Agent 的普通私聊房间相同，类型为 `PRIVATE`，通过 `biz_id` 约定标识：

```
biz_id = "CTRL:{agent_id}"
```

例如 agent_id=7 的控制房间 biz_id 为 `"CTRL:7"`。

此约定允许后端通过 `get_room_by_biz_id(team_id, f"CTRL:{agent_id}")` 快速查找，而不依赖 name 字符串匹配。

### 指令发送语义

通过活动视图发出的指令，语义等同于：在对应的私聊控制房间里，以 `insert_immediately=True` 发送一条 OPERATOR 消息。

---

## 3. 接口

新增接口：`POST /agents/{agent_id}/supervise.json`

**请求体：**

```json
{
  "content": "请先检查 stderr",
  "insert_immediately": true
}
```

`insert_immediately` 默认为 `true`，允许客户端显式传 `false`。

**成功响应：**

```json
{
  "room_id": 42,
  "created": true
}
```

- `room_id`：控制房间 ID（新建或已有）
- `created`：本次是否自动建立了新房间

**错误码：**

| 错误码 | 说明 |
|--------|------|
| `agent_not_found` | agent_id 不存在 |
| `team_not_active` | 所属 Team 未激活 |
| `control_room_not_ready` | 控制房间仍处于 INIT 状态（理论上不应发生） |
| `immediate_insert_driver_not_supported` | Agent driver 不支持即时注入（透传自 V20） |

---

## 4. 实现方案

### 4.1 服务层：get_or_create_control_room

新增 `roomService.get_or_create_control_room(team_id, agent_id) -> tuple[ChatRoom, bool]`，返回控制房间实例和是否新建标志。

逻辑（两步查找 + 按需创建）：

**第一步：按 biz_id 快速查找**

```python
gt_room = await gtRoomManager.get_room_by_biz_id(team_id, f"CTRL:{agent_id}")
```

找到则直接返回对应 ChatRoom（`created=False`）。

**第二步（fallback）：扫描现有 PRIVATE 房间**

若第一步未找到，扫描该 team 下所有 PRIVATE 房间：

```python
rooms = await gtRoomManager.get_rooms_by_team_and_type(team_id, RoomType.PRIVATE)
matched = next((r for r in rooms if agent_id in (r.agent_ids or [])), None)
```

若找到匹配房间（preset 预定义了该私聊房间，但未设 biz_id）：
1. 回填 `biz_id = f"CTRL:{agent_id}"` 并保存，方便后续快速查找
2. 返回对应 ChatRoom（`created=False`）

**第三步（实际创建）：两步均无结果**

执行以下步骤创建新控制房间：
1. 构造 `GtRoom`（`type=PRIVATE`, `name=f"{agent_name} 控制"`, `biz_id=f"CTRL:{agent_id}"`, `agent_ids=[agent_id]`, `max_turns=0`）
2. `gtRoomManager.save_room(gt_room)` 写库
3. `_load_room(gt_team, saved_room, [agent_id])` 注册内存 ChatRoom
4. `room.activate_scheduling()` 激活房间
5. 发布 `ROOM_ADDED` WS 事件（见 §4.2）
6. 返回 `(room, True)`

**注意事项：**

- 若 preset 中同一 agent 有多个 PRIVATE 房间（少见场景），fallback 取第一个匹配，并回填 biz_id。其他房间不受影响。
- 回填 biz_id 仅更新数据库记录，不影响运行中的 ChatRoom 内存状态。
- 新增 DAL 方法 `get_rooms_by_team_and_type(team_id, room_type)` 支持 fallback 查询。

### 4.2 新增 WS 事件：ROOM_ADDED

`MessageBusTopic.ROOM_ADDED`：通知前端有新房间加入当前 Team。

Payload：`gt_room(GtRoom)`, `team_id(int)`

触发时机：`get_or_create_control_room` 创建新房间后。

前端收到该事件后，将新房间追加到对应 Team 的房间列表（与 `ROOM_STATUS_CHANGED` 的更新路径共用 `updateTeamRooms`，但需先插入再更新）。

### 4.3 Controller 层

新建 `AgentSuperviseHandler`（`roomController.py` 或单独的 `superviseController.py`）：

```python
POST /agents/{agent_id}/supervise.json
```

逻辑：

1. 获取 `agent_id` 对应的 `GtAgent`，验证存在
2. 获取所属 `GtTeam`，验证 `enabled`
3. 验证 Agent driver 支持 `insert_immediately`（`host_managed_turn_loop`）
4. 调用 `roomService.get_or_create_control_room(team_id, agent_id)` 获取 ChatRoom
5. 验证 ChatRoom 状态不为 INIT
6. 调用 `room.add_message(OPERATOR_ID, content, insert_immediately=request.insert_immediately)`
7. 调用 `room.finish_turn(OPERATOR_ID)`
8. 返回 `{ room_id, created }`

路由注册：`src/route.py` 追加一行：

```python
(r"/agents/(\d+)/supervise.json", superviseController.AgentSuperviseHandler),
```

### 4.4 前端 API

`frontend/src/api.ts` 新增：

```typescript
export interface SuperviseResponse {
  room_id: number;
  created: boolean;
}

export async function superviseAgent(
  agentId: number,
  content: string,
  insertImmediately = true,
): Promise<SuperviseResponse> {
  return requestJson<SuperviseResponse>(`/agents/${agentId}/supervise.json`, {
    method: 'POST',
    body: JSON.stringify({ content, insert_immediately: insertImmediately }),
  });
}
```

### 4.5 前端 WS 处理：ROOM_ADDED

`eventNormalizer.ts` 新增事件类型 `room_added`：

```typescript
// FrontendRealtimeEvent union 新增分支
| { type: 'room_added'; teamId: number; room: RoomState }
```

`runtimeStore.ts` 新增 handler：

```typescript
case 'room_added':
  updateTeamRooms(event.teamId, (rooms) => {
    const exists = rooms.some((r) => r.room_id === event.room.room_id);
    return exists ? rooms : [...rooms, event.room];
  });
```

### 4.6 前端 UI：活动视图指令入口

在 Agent 活动面板（当前的 Console 视图或 Agent 详情面板）底部新增轻量指令输入区：

- 单行文本框 + "发送" 按钮
- 发送调用 `superviseAgent(agentId, content)`
- 发送成功后清空文本框，可选导航到对应控制房间
- 显示新房间创建提示（如 toast 通知）

具体组件位置和样式在前端设计阶段细化，本文档不约束实现细节。

---

## 5. 改动范围

| 文件 | 变更 |
|------|------|
| `src/constants.py` | 新增 `ROOM_ADDED` WS 事件 topic |
| `src/dal/db/gtRoomManager.py` | 新增 `get_rooms_by_team_and_type()` 方法 |
| `src/service/roomService/core.py` | 新增 `get_or_create_control_room()`，含两步 fallback 逻辑 |
| `src/controller/superviseController.py`（新文件） | `AgentSuperviseHandler` |
| `src/route.py` | 注册 `/agents/{agent_id}/supervise.json` 路由 |
| `src/controller/wsController.py` | 新增 `ROOM_ADDED` 事件映射 |
| `frontend/src/api.ts` | 新增 `superviseAgent()` 和 `SuperviseResponse` |
| `frontend/src/realtime/eventNormalizer.ts` | 新增 `room_added` 事件类型和 normalize 逻辑 |
| `frontend/src/realtime/runtimeStore.ts` | 新增 `room_added` 事件 handler |
| `frontend/src/components/...` | 活动视图新增指令输入区（具体组件待前端设计确认） |

---

## 6. 测试要点

- `get_or_create_control_room` 首次调用创建房间，第二次复用同一房间，biz_id 不冲突
- preset 预定义了 PRIVATE 房间（无 biz_id）时，fallback 扫描正确复用并回填 biz_id，不重复建房
- 同 agent 有多个 PRIVATE 房间时，fallback 取第一个，不报错
- 控制房间创建后 `ROOM_ADDED` WS 事件正确发布，payload 包含 gt_room 和 team_id
- `supervise.json` 接口：消息通过私聊房间以 `insert_immediately=True` 发送，行为与 V20 一致
- Agent 不支持 `host_managed_turn_loop` 时返回 `immediate_insert_driver_not_supported` 错误
- agent_id 不存在或 team 未激活时返回对应错误
- 前端收到 `ROOM_ADDED` 事件后，房间列表中出现新控制房间
- 重复发送追加指令不会重复建房
