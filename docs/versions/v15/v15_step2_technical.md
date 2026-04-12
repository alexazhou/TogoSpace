# V15: 房间工作中状态展示 - 技术文档

## 1. 方案概览

V15 的目标，是在**不改消息数据模型**的前提下，为前端增加“当前谁正在工作”的展示能力。

版本范围：

- `PRIVATE` 房间：显示当前 Agent 是否正在处理本轮对话
- `GROUP` 房间：显示当前轮到并正在处理任务的 Agent
- 不引入占位消息入库
- 不引入消息排队
- 不改现有 `room_messages` 结构

这版能力本质上是展示层增强，不是消息模型升级。

---

## 2. 状态来源

V15 复用现有运行态数据进行推断，不新增消息级状态。

建议展示判断优先使用：

- `room.need_scheduling`
- `room.state`
- `room.current_turn_agent`
- `agent.status`
- 必要时结合 `AgentActivityRecord`

### 2.1 房间通用判断

私聊和群聊统一按以下规则判断：

- `room.need_scheduling == true`
- `room.state == SCHEDULING`
- `room.current_turn_agent` 非空
- 对应 agent 当前处于 `ACTIVE`

满足时，前端展示：

- `当前处理中：{agent_name}`

若 agent 已失败或不再处于激活态，则不应继续显示“处理中”。

---

## 3. 前端展示规则

### 3.1 房间通用展示规则

- 在消息列表最后一条之后展示当前 Agent 的工作状态
- 不额外插入占位消息
- 最终回复仍沿用现有消息写入与显示逻辑

### 3.2 失败态

若当前处理中的 Agent 失败：

- 工作中提示应及时结束
- 可切换为短暂失败提示，或直接进入失败状态展示
- 不应继续误显示为“处理中”

---

## 4. 生命周期

V15 不介入消息处理流程，只监听现有房间状态与 Agent 状态变化，并驱动前端展示更新。

规则如下：

- 房间进入“可推断为处理中”的状态后，前端显示 `成员：“当前处理中”`
- 房间或 Agent 状态变化后，如果不再满足判断条件，前端移除该提示
- 若当前处理中的 Agent 失败，可将末尾状态切换为失败提示，或直接结束展示

---

## 5. 后端改动范围

V15 不改消息链路，直接复用现有房间状态和 Agent 状态。

已确认的后端改动如下：

| 文件 | 变更内容 |
|------|---------|
| `src/service/roomService.py` | `ChatRoom.to_dict()` 增加 `need_scheduling` 字段；`ROOM_STATUS_CHANGED` 的 payload 统一使用 `need_scheduling`、`state`、`current_turn_agent` |
| `src/controller/roomController.py` | 房间列表接口继续复用 `room.to_dict()` 返回 `need_scheduling` |
| `src/controller/wsController.py` | 继续转发现有 `room_status` / `agent_status` 事件，不新增新的 WS 事件类型 |
| `src/model/coreModel/gtCoreWebModel.py` | 若当前房间响应模型缺字段，补充 `need_scheduling`、`current_turn_agent` 等展示所需字段 |
| `frontend/src/types.ts` | `RoomInfo` / `RoomState` / `WsRoomStatusEvent` 统一补齐 `need_scheduling` 字段 |
| `frontend/src/realtime/eventNormalizer.ts` | `room_status` 事件归一化时读取 `need_scheduling` |
| `frontend/src/realtime/runtimeStore.ts` | 持久化 `need_scheduling`、`state`、`current_turn_agent`，并据此推导末尾工作状态 |
| `frontend/src/components/MessageStream.vue` | 在消息列表末尾展示 `成员：“当前处理中”` 或失败状态 |
| `tui/*` | 在房间视图的消息末尾展示同样的工作状态 |

---

## 6. 事件设计

V15 已确认直接复用现有事件链路，不新增事件类型。

使用的事件如下：

- `ROOM_STATUS_CHANGED`
  - 对外 WS 事件名：`room_status`
  - 关键字段：`gt_room`、`state`、`current_turn_agent`、`need_scheduling`
- `AGENT_STATUS_CHANGED`
  - 对外 WS 事件名：`agent_status`
  - 关键字段：`gt_agent`、`status`

前端判断规则固定为：

- `need_scheduling == true`
- `state == SCHEDULING`
- `current_turn_agent` 非空
- 当前 turn agent 的 `status == ACTIVE`

只要其中任一条件不满足，就移除 `成员：“当前处理中”`。

因此 V15：

- 不新增消息事件
- 不新增房间工作状态专用事件
- 不引入消息更新或消息删除事件

---

## 7. 测试要点

- 群聊房间能正确显示当前处理中成员
- 私聊房间能正确显示当前处理中状态
- Agent 成功后，工作状态能及时结束
- Agent 失败后，工作状态不会残留
- Web 与 TUI 对同一房间的工作状态判断一致
- 不引入任何消息顺序、消息数量上的回归

---

## 8. 结论

V15 先解决“当前谁在工作”这个展示层问题：

- 不改消息模型
- 不改消息写入链路
- 私聊和群聊都受益
- 为后续 V16 的私聊消息占位升级打基础
