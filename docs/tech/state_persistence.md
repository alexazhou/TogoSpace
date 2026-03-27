# 状态持久化与恢复

本文描述系统在运行时如何保存状态到数据库，以及重启后如何恢复。

---

## 持久化的状态

系统有两类运行时状态需要跨进程保存：

| 对象 | 状态内容 | 存储位置 |
|------|---------|---------|
| `Agent` | LLM 对话历史（`_history`） | `agent_histories` 表 |
| `ChatRoom` | 聊天记录（`messages`）+ 各成员读取进度（`_member_read_index`） | `room_messages` 表 + `rooms.member_read_index` 列 |

---

## 保存时机

### 成员对话历史

每当成员追加一条消息时（无论是用户输入、LLM 回复还是 Tool 结果），都**同步写入**数据库：

```
Agent.append_history_message(msg)
  └── _persist_history_message(msg)
        └── persistenceService.append_agent_history_message(GtAgentHistory)
              └── gtAgentHistoryManager.append_agent_history_message()
```

`GtAgentHistory` 记录 `agent_id`、`seq`（顺序号）、`message_json`（Pydantic JSON 序列化）。

### 房间聊天记录

`ChatRoom.add_message()` 被调用时追加写入，但有一个门卫条件：**房间处于 `INIT` 状态时不写库**。

```
ChatRoom.add_message(sender, content)
  ├── [INIT 状态] → 仅写内存，跳过持久化
  └── [非 INIT]  → persistenceService.append_room_message()
                      └── gtRoomMessageManager.append_room_message()
```

这避免了每次启动时写入初始系统消息（房间创建时总处于 INIT）。

### 成员读取进度

每当成员读取消息时（`ChatRoom.get_unread_messages()`），读取位置推进后**异步写入**：

```
ChatRoom.get_unread_messages(agent_name)
  └── 推进 _member_read_index[agent_name]
        └── persistenceService.save_room_runtime(room_id, id_keyed_index)
              └── gtRoomManager.save_room_state()  → 写入 rooms.member_read_index
```

存储时 key 从成员名转换为 `member_id`（字符串形式），以解耦成员改名场景。

---

## 恢复时机

恢复发生在启动阶段 4，位于房间创建完成、`exit_init_rooms()` 之前：

```
backend_main.py（阶段 4）
  ├── agentService.restore_state()
  └── roomService.restore_state()
        └── roomService.exit_init_rooms()   ← 恢复完成后才激活
```

### 成员历史恢复（`agentService.restore_state`）

```
for each Agent:
    items = persistenceService.load_agent_history_message(agent_id)
    if items:
        agent.inject_history_messages(items)   # 直接替换 _history 列表
```

若某成员在数据库中无历史记录（首次启动或新成员），跳过注入，`_history` 保持空列表。

### 房间状态恢复（`roomService.restore_state`）

每个房间按以下逻辑处理：

```
room_msg_rows, member_read_index = load_room_runtime(room_id)

情况 A：数据库有聊天记录
  → 将 GtRoomMessage 行转换为 GtCoreChatMessage 列表
  → inject_runtime_state(messages=..., member_read_index=...)
  → rebuild_state_from_history()   # 重放消息推演轮次状态

情况 B：数据库无记录，房间内存也为空（理论上不会发生，因为创建时写了初始消息）
  → add_message(SYSTEM, build_initial_system_message())

情况 C：数据库无记录，房间内存已有内容（首次启动）
  → 仅调用 rebuild_state_from_history()，不注入
```

`inject_runtime_state` 在还原 `_member_read_index` 时会把 key 从 `member_id` 字符串反查回成员名，兼容旧格式（key 已经是名称时直接使用）。

`rebuild_state_from_history` 重放所有消息以推演出正确的轮次计数器状态（`_turn_index`、`_turn_pos`、`_state`），确保重启后调度逻辑的连续性。

---

## 数据流总览

```
运行时写入：

  Agent._history  ──append──►  agent_histories 表
  ChatRoom.messages    ──append──►  room_messages 表
  ChatRoom._member_read_index  ──save──►  rooms.member_read_index

启动恢复：

  agent_histories 表  ──load──►  Agent._history
  room_messages 表   ──load──►  ChatRoom.messages
  rooms.member_read_index  ──load──►  ChatRoom._member_read_index
                                           └── rebuild_state_from_history()
```

---

## 注意事项

- **INIT 状态门卫**：房间在 `exit_init_rooms()` 前不会持久化消息。恢复完成后才调用 `exit_init_rooms()`，保证恢复期间写入的初始系统消息不被重复持久化。
- **member_id 作为 key**：`_member_read_index` 存储时以 `member_id` 为 key，恢复时通过 `_member_name_map` 反查名称。若成员被删除后重建，`member_id` 可能变化，旧的读取进度会丢失（视为从头读）。
- **seq 顺序保证**：`agent_histories` 的 `seq` 字段在写入时由 `len(_history) - 1` 计算，恢复时通过 `gtAgentHistoryManager.get_agent_history` 按 `seq` 升序读取，保证顺序正确。
