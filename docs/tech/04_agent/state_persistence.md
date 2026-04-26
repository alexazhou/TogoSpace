# 状态持久化与恢复

本文描述系统如何把运行过程中的关键状态写入数据库，以及在进程重启或 Team runtime 重建后如何恢复。

---

## 总体原则

系统把“配置”和“运行状态”拆开处理：

- 配置：Team / Agent / Room 的定义信息，存放在各自的业务表中
- 运行状态：Agent history、Room messages、Room read index、turn_pos 等，单独持久化

恢复时也分两步：

1. `load_xxx`：从数据库读取配置，创建内存对象
2. `restore_xxx_runtime_state`：把持久化的运行状态灌回这些已创建的内存对象

Team 这一层是总控编排，不直接使用 `restore_team_runtime_state` 这种命名，因为它包含的不只是 state restore，还包括 load 和调度启动。当前统一命名见文末“命名规则”。

---

## 持久化的状态

当前需要跨进程保存的运行状态如下：

| 对象 | 状态内容 | 存储位置 |
| --- | --- | --- |
| `Agent` | LLM 对话历史 | `agent_histories` |
| `Agent` | 遗留任务状态 | `agent_tasks` |
| `ChatRoom` | 房间消息 | `room_messages` |
| `ChatRoom` | `agent_read_index` | `rooms.agent_read_index` |
| `ChatRoom` | `turn_pos` | `rooms.turn_pos` |

说明：

- `Agent` 的 system prompt、driver 配置、工作目录不属于 runtime state，它们来自 Team/Agent 配置
- `ChatRoom` 的成员列表、房间名、房间类型、初始 topic、`max_turns` 也属于配置，不属于 runtime state

---

## 写入时机

### Agent history

每当 Agent history 追加消息时，会同步写入 `agent_histories`：

```text
AgentHistoryStore.append_history_message(...)
  └── gtAgentHistoryManager.append_agent_history_message(...)
```

写入内容包括：

- `agent_id`
- `seq`
- `message_json`
- `status`
- `usage`

### Room messages

房间消息通过 `ChatRoom.add_message()` / `_append_message()` 追加。

当房间处于 `INIT` 状态时：

- 只写内存
- 不写 `room_messages`

当房间已激活后：

- 追加到 `room_messages`
- 广播 `ROOM_MSG_ADDED`
- 根据消息来源更新轮次状态

### Room read index / turn_pos

房间运行中会把这两个状态写回 `rooms` 表：

- `agent_read_index`
- `turn_pos`

典型写入路径：

```text
ChatRoom.get_unread_messages(...)
  └── gtRoomManager.update_room_state(...)

ChatRoom.finish_turn(...)
  └── gtRoomManager.update_room_state(...)
```

其中 `agent_read_index` 存储时以 `agent_id` 的字符串形式作为 key。

---

## 恢复入口

### 进程启动

进程启动后，当前恢复链路是：

```text
backend_main.py
  └── for team in gtTeamManager.get_all_teams(enabled=True)
        └── teamService.restore_team(team.id, ...)
              ├── agentService.load_team_agents(...)
              ├── roomService.load_team_rooms(...)
              ├── agentService.restore_team_agents_runtime_state(...)
              ├── roomService.restore_team_rooms_runtime_state(...)
              └── schedulerService.start_scheduling(...)
```

### Team runtime 重建

Team 热更新或 Team 级重启时，当前走的是同一条 Team runtime 生命周期：

```text
teamService.restart_team_runtime(team_id)
  ├── stop_team_runtime(team_id)
  └── restore_team(team_id)
```

这意味着：

- 应用重启：按 Team 逐个调用 `restore_team(team.id, ...)`
- Team 热更新：调用 `restart_team_runtime(team.id)`

两条路径最终都会落到同一套 Team runtime 生命周期上，只是入口不同：

- 应用重启没有旧内存对象需要停止，所以直接 `restore_team`
- Team 热更新需要先停掉旧 runtime，再重新 `restore_team`

这样“进程重启恢复”和“Team runtime 重建恢复”在语义上保持一致，避免出现一条链路会恢复 history，另一条不会恢复的问题。

---

## Agent 恢复

### load：创建内存 Agent

`agentService.load_team_agents(team_id)` 做的事情是：

- 从数据库读取 Team 下 Agent 配置
- 读取 role template
- 构建 system prompt / driver config / workdir
- 创建内存 `Agent` 实例

这一步只创建对象，不恢复 history。

### restore runtime state：恢复 history 和 task 状态

`agentService.restore_team_agents_runtime_state(team_id, ...)` 会遍历该 Team 已经存在的内存 Agent，并调用：

```text
_restore_agent_runtime_state(agent, ...)
  ├── persistenceService.load_agent_history_message(agent_id)
  ├── agent.inject_history_messages(items)
  ├── persistenceService.fail_running_tasks(agent_id, error_message=...)
  └── 按 task 状态回填 agent.task_consumer.status
```

行为要点：

- history 从数据库直接注回内存
- 遗留 `RUNNING` task 会在恢复时统一标记为 `FAILED`
- 失败原因会区分恢复场景：
  - 进程重启：`task interrupted by process restart`
  - Team runtime 重建：`task interrupted by team runtime restart`

---

## Room 恢复

### load：创建内存 Room

`roomService.load_team_rooms(team_id)` 做的事情是：

- 从数据库读取 Team 下 Room 配置
- 先关闭当前 Team 旧的内存 Room
- 重新创建内存 `ChatRoom`

这一步恢复的是“房间骨架”：

- 房间定义
- 成员列表
- 类型
- `initial_topic`
- `max_turns`

这一步不恢复 messages / read index / turn_pos。

### restore runtime state：恢复消息与轮次进度

`roomService.restore_team_rooms_runtime_state(team_id)` 会对已存在的内存 Room 调用：

```text
_restore_room_runtime_state(room)
  ├── gtRoomMessageManager.get_room_messages(room_id)
  ├── gtRoomManager.get_room_state(room_id)
  ├── room.inject_runtime_state(messages=..., agent_read_index=..., turn_pos=...)
  └── room.rebuild_state_from_history(persisted_turn_pos=...)
```

当前恢复的核心状态是：

- `messages`
- `agent_read_index`
- `turn_pos`

说明：

- `rebuild_state_from_history()` 不会逐条回放消息重新驱动业务逻辑
- 它只基于恢复后的持久化数据重建内存中的轮次状态
- Room 在完成 restore 之后，才会由 `schedulerService.start_scheduling()` 统一激活

---

## 为什么要分成 load 和 restore

这是当前 runtime 生命周期里最重要的边界。

### `load_xxx`

职责是：

- 从数据库读取配置
- 创建内存对象

典型例子：

- `agentService.load_team_agents`
- `roomService.load_team_rooms`

### `restore_xxx_runtime_state`

职责是：

- 在内存对象已存在的前提下
- 把运行进度恢复回来

典型例子：

- `agentService.restore_team_agents_runtime_state`
- `roomService.restore_team_rooms_runtime_state`

如果只有 `load` 没有 `restore`，结果就是：

- Team / Agent / Room 对象存在
- 但它们会像“新建对象”一样丢失之前的运行上下文

---

## 命名规则

当前相关方法统一使用以下命名规则。

### 1. `load_xxx`

含义：

- 从数据库或配置读取定义信息
- 创建内存对象

例子：

- `agentService.load_team_agents`
- `agentService.load_all_team_agents`
- `roomService.load_team_rooms`
- `roomService.load_all_rooms`

### 2. `restore_xxx_runtime_state`

含义：

- 恢复已经存在的内存对象的运行状态

例子：

- `agentService.restore_team_agents_runtime_state`
- `agentService.restore_all_agents_runtime_state`
- `roomService.restore_team_rooms_runtime_state`
- `roomService.restore_all_rooms_runtime_state`

### 3. `stop_xxx_runtime`

含义：

- 停止某个业务对象对应的整套 runtime

例子：

- `teamService.stop_team_runtime`

注意：

- `schedulerService.stop_scheduler_team` 不属于这一类
- 它只停止 scheduler/consumer，不停止整个 Team runtime

### 4. `restore_team`

含义：

- Team 级总控恢复入口
- 内部包含 load、restore state、启动调度三个动作

当前流程：

```text
restore_team(team_id)
  ├── load_team_agents(...)
  ├── load_team_rooms(...)
  ├── restore_team_agents_runtime_state(...)
  ├── restore_team_rooms_runtime_state(...)
  └── start_scheduling(...)
```

因为它不只是“恢复 state”，所以这里不用 `restore_team_runtime_state` 命名。

### 5. `restart_xxx_runtime`

含义：

- stop + restore

例子：

- `teamService.restart_team_runtime`

---

## 当前恢复链路总结

```text
Team runtime 生命周期：

  stop_team_runtime(team_id)
    ├── schedulerService.stop_scheduler_team(team_id)
    ├── agentService.unload_team(team_id)
    └── roomService.close_team_rooms(team_id)

  restore_team(team_id)
    ├── agentService.load_team_agents(team_id)
    ├── roomService.load_team_rooms(team_id)
    ├── agentService.restore_team_agents_runtime_state(team_id)
    ├── roomService.restore_team_rooms_runtime_state(team_id)
    └── schedulerService.start_scheduling(team.name)

  restart_team_runtime(team_id)
    ├── stop_team_runtime(team_id)
    └── restore_team(team_id)
```

统一入口关系：

```text
应用重启
  └── for each enabled team:
        └── restore_team(team.id)

Team 热更新
  └── restart_team_runtime(team.id)
        ├── stop_team_runtime(team.id)
        └── restore_team(team.id)
```

---

## 注意事项

- 房间在 `INIT` 状态下不会把初始系统消息写入 `room_messages`
- `agent_read_index` 以 `agent_id` 字符串形式写库，恢复时再转回 int
- Team 热更新和进程启动现在共用同一套 Team runtime 恢复语义
- 如果只重建内存对象但不恢复 runtime state，会出现“房间读指针还在，但 Agent history 丢失”的上下文断裂问题
