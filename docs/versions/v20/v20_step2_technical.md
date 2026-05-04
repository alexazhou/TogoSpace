# V20: 运行中上下文插入能力 - 技术文档

## 1. 目标

V20 解决两个私聊消息时序问题：

1. **即时插入**：当前私聊消息只在 turn 开始时被 Agent 读取。若 Agent 已在推理/工具调用中，人类的补充信息需等待整轮结束。引入 `insert_immediately` 参数，在安全边界将消息注入当前 turn。

2. **普通消息时序修正**：私聊房间 Agent 工作期间（SCHEDULING 状态）发送的普通消息，当前会立即分配 seq 并广播，导致时间线上出现在 Agent 回复之前。V20 改为延迟分配 seq，确保出现在 Agent 回复之后。

范围约束：
- 仅 `PRIVATE` 房间
- 仅 `host_managed_turn_loop == True` 的 driver
- `GROUP` 房间或不支持的 driver 传入 `insert_immediately=true` 直接返回错误

---

## 2. 接口

`POST /rooms/{room_id}/messages/send.json` 请求体扩展：

```json
{
  "content": "先检查 stderr",
  "insert_immediately": true
}
```

行为规则：

| `insert_immediately` | 场景 | 行为 |
|---|---|---|
| `false` | 非 PRIVATE，或非 SCHEDULING | 保持现有逻辑 |
| `false` | PRIVATE + SCHEDULING | 走 queued 路径（§3.2） |
| `true` | 非 PRIVATE 房间 | 返回错误 |
| `true` | 无活跃 Agent turn | 按普通私聊流程处理 |
| `true` | driver 不支持 | 返回错误 |
| `true` | PRIVATE + host-managed + 活跃 turn | 走即时插入流程（§3.3） |

新增错误码：`room_immediate_insert_not_supported`、`immediate_insert_driver_not_supported`。

---

## 3. 实现方案

### 3.1 两类 pending 消息

私聊房间 Agent 工作期间，新消息以 `seq=None` 暂存，按 `insert_immediately` 分两种路径 flush：

| 类型 | 条件 | flush 时机 | 时间线位置 |
|------|------|-----------|----------|
| 即时插入 | `insert_immediately=true` + SCHEDULING | turn 内安全边界 | Agent 回复**之前** |
| queued | `insert_immediately=false` + OPERATOR + PRIVATE + SCHEDULING | turn 结束后 | Agent 回复**之后** |

两者在 messageStore 中均以 seq=None 存储（`append_pending`），由各自 flush 方法分配 seq（`assign_seq`）并广播。

### 3.2 queued 消息路径

**写入阶段**（`ChatRoom._append_message`）：

满足以下条件时，调用 `store.append_pending(message)` 替代 `append_and_assign_seq`：
- `insert_immediately=False` 且 `sender_id == OPERATOR_MEMBER_ID`
- 房间类型为 `PRIVATE` 且状态为 `SCHEDULING`

消息以 `seq=None` 存储，不立即广播 WS 事件。

**flush 阶段**（`ChatRoom.flush_queued_messages`）：

由 `AgentTurnRunner` 在 turn 结束后调用：分配 seq、更新 DB、广播 WS，最后以 OPERATOR 身份调用 `finish_turn` 触发下一轮调度。

```python
# agentTurnRunner.run_chat_turn 内：
await self._run_turn_loop(room)
await room.flush_queued_messages()
```

自动跳过路径（`synced_count == 0` 时 skip）也在 `finish_turn` 后执行 `flush_queued_messages`，处理 dispatch 与 turn 检查之间的竞态。

### 3.3 即时插入路径

`_run_turn_loop()` 在每个 step 前检查 `has_pending_immediate_messages(agent_id)`。该方法仅检查 `insert_immediately=True` 且未读的 pending 消息。

若为 true，调用 `room.get_unread_messages(agent_id)` 拉取未读消息，构造为 `USER` history 追加，继续推进。

**允许插入**的安全边界状态（当前批次已完成）：
- `USER` / `SYSTEM`
- `ASSISTANT(SUCCESS)` 且无待执行 `tool_calls`
- `TOOL(SUCCESS/FAILED/CANCELLED)` 且整批 `tool_calls` 已全部收尾

**不允许插入**的状态（当前批次未完成）：
- `ASSISTANT(INIT)` — 推理进行中
- `TOOL(INIT)` — 工具执行中
- `ASSISTANT(SUCCESS, tool_calls=...)` 且 tool chain 未执行完

核心约束：**不打断已产出的 tool_calls 批次**，否则破坏 tool_call ↔ tool_result 的消息顺序。

### 3.4 防止重复同步

`get_unread_messages(agent_id)` 按原有逻辑自动推进 read index，这批消息被当前 turn 消费后，下次 turn 开始不会重复同步。

### 3.5 Prompt 构造

新增 `promptBuilder.build_turn_update_prompt_from_messages()`，与 `build_turn_begin_prompt_from_messages()` 的区别是不带 `ROOM_TURN_BEGIN` tag，语义为"房间里出现了新的补充信息"。多条消息合并为一条 `USER` 消息追加。

---

## 4. 改动范围

| 文件 | 变更 |
|------|------|
| `src/controller/roomController.py` | `SendMessageRequest` 加 `insert_immediately`，扩展发送逻辑 |
| `src/service/roomService/messageStore.py` | `has_pending_immediate_messages`；flush 方法（queued + inject） |
| `src/service/roomService/chatRoom.py` | `_append_message` queued 分支；`flush_queued_messages()` |
| `src/service/agentService/agentTurnRunner.py` | step 前检查即时插入；turn 结束后和 skip 路径调用 queued flush |
| `src/service/agentService/agentHistoryStore.py` | tool batch 闭合判断辅助方法 |
| `src/service/agentService/promptBuilder.py` | `build_turn_update_prompt_from_messages()` |

---

## 5. 测试要点

- PRIVATE 房间 `insert_immediately=true`，消息在 turn 内安全边界生效
- 工具执行中不中断，整批完成后才插入
- 消息插入不会发生在 `assistant(tool_calls)` 与对应 `tool` 结果之间
- 已插入消息不会被后续 `get_unread_messages()` 重复同步
- GROUP 房间和不支持的 driver 传入 `insert_immediately=true` 返回错误
- PRIVATE + SCHEDULING 下普通消息 queued，seq 在 Agent 回复后分配
- 自动跳过轮次时，queued 消息也能正确 flush 并触发调度
