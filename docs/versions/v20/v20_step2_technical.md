# V20: 运行中上下文插入能力 - 技术文档

## 1. 目标

当前私聊消息只在 turn 开始时被 Agent 读取。若 Agent 已在推理/工具调用中，人类即使补充了关键信息也需等待整轮结束。

V20 为私聊发消息接口增加 `insert_immediately` 参数：消息写入房间后，若满足条件，在当前 turn 的安全边界尽快注入 Agent 的对话上下文，不打断正在执行的推理或工具调用。

范围约束：
- 仅 `PRIVATE` 房间
- 仅 `host_managed_turn_loop == True` 的 driver
- `GROUP` 房间或不支持的 driver 传入该参数直接返回错误

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
| `false` | — | 保持现有逻辑 |
| `true` | 非 PRIVATE 房间 | 返回错误 |
| `true` | 无活跃 Agent turn | 按普通私聊流程处理 |
| `true` | driver 不支持 | 返回错误 |
| `true` | PRIVATE + host-managed + 活跃 turn | 走运行中插入流程 |

新增错误码：`room_immediate_insert_not_supported`、`immediate_insert_driver_not_supported`。

---

## 3. 实现方案

### 3.1 房间侧即时消息检查

消息按正常流程先写入房间，并将 `insert_immediately` 作为消息属性存储。房间侧提供一个轻量检查接口，例如：

```python
has_pending_immediate_messages(agent_id: int) -> bool
```

语义：

- 只针对当前房间判断
- 只检查是否存在 `insert_immediately=true` 且当前 Agent 尚未读取的消息
- 不直接返回消息内容，也不推进 read index

### 3.2 安全边界插入

`_run_turn_loop()` 在每个 step 前先检查当前房间是否存在待即时插入消息。

若 `has_pending_immediate_messages(agent_id)` 返回 `true`，则直接复用当前房间已有的：

```python
room.get_unread_messages(agent_id)
```

把这批未读消息拉出，构造成一次运行中补充消息，并在安全边界追加为 `USER` history 消息，然后继续推进。

**允许插入**的状态（当前批次已完成）：
- `USER` / `SYSTEM`
- `ASSISTANT(SUCCESS)` 且无待执行 `tool_calls`
- `TOOL(SUCCESS/FAILED/CANCELLED)` 且整批 `tool_calls` 已全部收尾

**不允许插入**的状态（当前批次未完成）：
- `ASSISTANT(INIT)` — 推理进行中
- `TOOL(INIT)` — 工具执行中
- `ASSISTANT(SUCCESS, tool_calls=...)` 且后续 tool chain 未执行完

核心约束：**不打断已产出的 tool_calls 批次**。一旦推理产出了工具调用，必须等整批执行完毕才能插入，否则会破坏 tool_call ↔ tool_result 的消息顺序。

### 3.3 防止重复同步

一旦在安全边界调用了当前房间的 `get_unread_messages(agent_id)`，该房间中这批消息的 read index 会按原有逻辑自然推进。这样这些消息已经被当前 turn 提前消费，下一次 turn 开始时不会再次被同步。

### 3.4 Prompt 构造

新增 `promptBuilder.build_turn_update_prompt_from_messages()`，与 `build_turn_begin_prompt_from_messages()` 的区别是不带 `ROOM_TURN_BEGIN` tag，语义为“房间里出现了新的补充信息”，而非“新轮开始”。多条消息合并为一条 `USER` 消息一次性追加。

---

## 4. 改动范围

| 文件 | 变更 |
|------|------|
| `src/controller/roomController.py` | `SendMessageRequest` 加 `insert_immediately`，扩展发送逻辑 |
| `src/service/roomService/chatRoom.py` | 新增 `has_pending_immediate_messages(agent_id)` |
| `src/service/agentService/agentTurnRunner.py` | 安全边界检查房间是否存在待插入消息；复用 `get_unread_messages()` 拉取并追加插入消息 |
| `src/service/agentService/agentHistoryStore.py` | tool batch 闭合判断辅助方法 |
| `src/service/agentService/promptBuilder.py` | `build_turn_update_prompt_from_messages()` |

---

## 5. 测试要点

- PRIVATE 房间 `insert_immediately=true` 时，消息在当前 turn 内生效而非等下一轮
- 工具执行中插入不中断当前工具，等整批完成后再生效
- 消息插入在 `assistant(tool_calls)` 与对应 `tool` 结果之间不会发生
- 已插入消息不会在后续 turn 开始时被 `get_unread_messages()` 重复同步
- GROUP 房间和不支持的 driver 传入该参数返回错误
