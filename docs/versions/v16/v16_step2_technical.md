# V16: 私聊消息排队与占位提交 - 技术文档

## 1. 方案概览

V16 只对 `PRIVATE` 房间启用**消息状态栅栏**方案：

```text
PRIVATE 房间消息统一先写入 room_messages
        │
        ├── SENT      已发送，可见，可被 Agent 读取
        ├── TYPING    正在输入，前端可见，作为当前栅栏点
        └── PENDING   已入库但暂不可见，等待前面的 TYPING 结束
```

`GROUP` 房间保持现状：

- 消息继续直接写入 `SENT`
- 不创建 `TYPING`
- 不使用 `PENDING`

这套方案的目标，是让私聊房间的输入中占位直接进入消息模型，而不是只停留在前端展示层。

---

## 2. 数据模型

### 2.1 `room_messages` 扩展

`GtRoomMessage` 新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `RoomMessageStatus` | 消息状态 |

建议状态定义：

```python
class RoomMessageStatus(EnhanceEnum):
    SENT = auto()
    TYPING = auto()
    PENDING = auto()
```

### 2.2 前后端消息模型补充

房间消息统一增加：

- `id`
- `status`

涉及模型：

- `GtCoreRoomMessage`
- `GtCoreMessageInfo`
- `GtCoreWebModel`
- 前端 `MessageInfo`

---

## 3. 状态规则

### 3.1 私聊房间约束

- 每个 `PRIVATE` 房间同一时刻最多只有一条 `TYPING`
- `TYPING` 是当前可见性栅栏
- 它后面的消息只能是 `PENDING`

### 3.2 可见性规则

前端展示：

- 显示连续的 `SENT`
- 若遇到第一条 `TYPING`，也显示它
- 该 `TYPING` 之后的 `PENDING` 不显示

Agent 同步：

- 只读取 `SENT`

### 3.3 推进规则

当一条 `TYPING` 结束时：

1. 当前消息结束阻塞
2. 更新为最终状态
3. 检查其后的第一条 `PENDING`
4. 将其推进为新的 `TYPING` 或 `SENT`

---

## 4. 生命周期

### 4.1 Agent 开始处理私聊房间任务

- 若当前房间没有 `TYPING`
  - 插入一条 `TYPING`
- 若当前房间已有 `TYPING`
  - 插入一条 `PENDING`

### 4.2 当前房间 `send_chat_msg`

`PRIVATE` 房间内：

- 不再额外新增新消息
- 只更新本轮对应的 `TYPING / PENDING` 消息内容

第一版约束：

- 同一轮多次 `send_chat_msg`
- 后一次直接覆盖前一次内容

### 4.3 `finish_chat_turn`

- 当前 `TYPING -> SENT`
- 内容保留为最终文本
- 然后释放后续 `PENDING`

### 4.4 失败

- 当前 `TYPING` 更新为失败说明
- 状态改为 `SENT`
- 然后释放后续 `PENDING`

固定文案建议：

- `"{agent_name} 运行失败，请稍后重试"`

### 4.5 取消 / 未发言

- 当前 `TYPING` 直接删除
- 然后释放后续 `PENDING`

---

## 5. 重启恢复

恢复来源：

- 统一从 `room_messages` 读取

恢复规则：

- 前端展示流：连续 `SENT` + 第一条 `TYPING`
- Agent 逻辑流：仅 `SENT`

若启动时仍有残留 `TYPING`：

- 统一更新为失败说明
- 状态改为 `SENT`
- 再继续释放后续 `PENDING`

---

## 6. 代码改动范围

| 文件 | 变更内容 |
|------|---------|
| `src/model/dbModel/gtRoomMessage.py` | 增加 `status` |
| `src/model/coreModel/gtCoreChatModel.py` | 消息模型增加 `id` / `status` |
| `src/model/coreModel/gtCoreWebModel.py` | 房间消息响应增加 `id` / `status` |
| `src/dal/db/gtRoomMessageManager.py` | 增加创建、更新、删除、按状态读取 |
| `src/service/roomService.py` | 增加私聊房间状态栅栏相关方法与恢复逻辑 |
| `src/service/funcToolService/tools.py` | 调整私聊房间当前房间 `send_chat_msg` / `finish_chat_turn` 语义 |
| `src/service/agentService/agentTurnRunner.py` | 私聊房间创建和推进 `TYPING/PENDING` |
| `src/controller/roomController.py` | 返回带 `id/status` 的展示消息 |
| `src/controller/wsController.py` | 增加消息更新/删除事件 |
| `frontend/src/types.ts` | 扩展消息类型 |
| `frontend/src/realtime/eventNormalizer.ts` | 增加消息更新/删除归一化 |
| `frontend/src/realtime/runtimeStore.ts` | 支持消息原地更新和删除 |
| `frontend/src/components/MessageStream.vue` | 按 `message.id` 渲染，支持 `TYPING` 样式 |

---

## 7. 事件设计

当前只有：

- `ROOM_MSG_ADDED`

V16 新增：

- `ROOM_MSG_UPDATED`
- `ROOM_MSG_DELETED`

对应 WS 事件：

- `message`
- `message_updated`
- `message_deleted`

---

## 8. 测试要点

- 私聊房间第一条占位消息插入为 `TYPING`
- 私聊房间已有 `TYPING` 时，新消息插入为 `PENDING`
- 当前房间 `send_chat_msg` 只更新当前消息，不新增新消息
- `finish_chat_turn` 后 `TYPING -> SENT`
- 失败时 `TYPING -> SENT(失败说明)`
- 取消时删除当前 `TYPING`
- 后续 `PENDING` 能继续推进
- Agent unread / history 同步只读取 `SENT`
- 重启恢复时残留 `TYPING` 会被转成失败说明
- 前端能按 `message.id` 原地更新和删除

---

## 9. 结论

V16 的核心，是只在 `PRIVATE` 房间把消息扩展成三态：

- `SENT`
- `TYPING`
- `PENDING`

在这套模型下：

- 私聊输入中体验可以直接进入消息流
- 消息顺序稳定
- 前端替换简单
- 重启恢复也更直接
