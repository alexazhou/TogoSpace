# Agent 调度逻辑说明

本文档基于当前实现梳理调度链路，主要对应：

- `src/service/roomService.py`
- `src/service/schedulerService.py`
- `src/service/agentService/agent.py`
- `src/service/funcToolService/tools.py`

## 1. 核心角色

- `ChatRoom`：房间级状态机，维护当前发言位、轮次计数、跳过窗口与 `INIT / SCHEDULING / IDLE` 状态。
- `schedulerService`：订阅 `ROOM_AGENT_TURN`，为目标 Agent 创建数据库任务记录，并按需拉起消费协程。
- `Agent`：串行消费自己的数据库任务，执行一轮推理、工具调用与收尾。
- `funcToolService`：提供 `send_chat_msg` / `finish_chat_turn` 等工具，驱动 turn 真正推进。

当前实现里，Scheduler 不再维护独立的运行中 Agent / Task 列表，消费协程句柄归 `Agent.consumer_task` 自身持有。

## 2. 关键状态

### 2.1 ChatRoom

- `_turn_pos`：当前发言位在 `_agent_names` 中的索引。
- `_turn_count`：完成整圈发言后的轮次计数。
- `_current_turn_has_content`：当前发言位是否已经产出过真实消息。
- `_round_skipped_set`：自上次真实消息以来，已跳过发言的成员集合。
- `_state`：`INIT / SCHEDULING / IDLE`。
- `_state_after_init`：`INIT` 退出后应恢复到的目标状态。

### 2.2 Agent

- `status`：`ACTIVE / IDLE / FAILED`。
- `consumer_task`：当前 Agent 的消费协程句柄。
- `current_db_task`：当前认领中的数据库任务记录（`GtAgentTask`）。

### 2.3 消息总线事件

- `ROOM_AGENT_TURN`：轮到某个普通 Agent 发言；仅对非 `SpecialAgent` 发布。当前 payload 为 `gt_agent / room_id`。
- `ROOM_MSG_ADDED`：房间新增消息；payload 为 `gt_room / sender / content / time`。
- `AGENT_STATUS_CHANGED`：Agent 状态变更；payload 为 `gt_agent / status`。

## 3. 启动与恢复

### 3.1 启动顺序

`backend_main.main()` 的调度相关启动顺序是：

1. `agentService.load_all_team()`
2. `roomService.load_rooms_from_db()`
3. `agentService.restore_state()`
4. `roomService.restore_state()`
5. `schedulerService.start_scheduling()`

其中：

- `roomService.restore_state()` 会从持久化消息和已读进度重建房间运行态。
- `schedulerService.start_scheduling()` 只负责调用 `roomService.activate_rooms()`，统一激活房间调度。

### 3.2 Team 热更新

`teamService.hot_reload_team()` 当前顺序是：

1. `schedulerService.stop_team(team.id)`
2. `agentService.reload_team(team.id)`
3. `roomService.refresh_rooms_for_team(team.id)`
4. `schedulerService.start_scheduling(team_name)`

也就是说，热更新会先停掉旧消费者，再重建 Team 下的 Agent 与 Room 运行态，最后重新触发调度。

## 4. 单个 Turn 的生命周期

### 4.1 房间发布轮次

当房间进入 `SCHEDULING`，或当前发言人 `finish_turn()` 成功后，`ChatRoom` 会：

1. 调用 `_resolve_next_dispatchable_agent()` 解析下一位可调度 Agent
2. 若返回 `None`，表示命中停止条件或当前需等待特殊成员输入
3. 若返回 Agent 名，调用 `_publish_current_turn()` 发布 `ROOM_AGENT_TURN`

注意：

- `_resolve_next_dispatchable_agent()` 内部处理两层过滤：
  - 自动跳过：GROUP 房间中的 `OPERATOR`（满足条件时自动推进到下一位）
  - 等待输入：任何 `SpecialAgent` 当前发言位都会返回 `None`，等待外部调用 `finish_turn()`
- `_publish_current_turn()` 只负责发布事件，不再检查 SpecialAgent（由上层保证传入的都是普通 Agent）

### 4.2 Scheduler 创建数据库任务

`schedulerService._on_agent_turn()` 收到 `ROOM_AGENT_TURN` 后会：

1. 从 payload 中读取 `gt_agent`
2. 检查该 Agent 是否已经存在同一 `room_id` 的 `PENDING` 数据库任务
3. 若无重复，则创建 `GtAgentTask(type=ROOM_MESSAGE, task_data={"room_id": room_id})`
4. 调用 `agent.start_consumer_task()`

这里的去重粒度是“同一 Agent、同一房间、仍处于 `PENDING` 的数据库任务”。

### 4.3 Agent 消费数据库任务

`Agent.start_consumer_task()` 只负责保证消费协程存在：

- 若 `consumer_task` 仍在运行，则直接跳过
- 否则创建 `asyncio.create_task(self.consume_task())`

`Agent.consume_task()` 的主要流程：

1. 将 `status` 置为 `ACTIVE` 并发布 `AGENT_STATUS_CHANGED`
2. 循环读取该 Agent 的首个 `PENDING` 任务
3. 通过 `claim_task()` 原子认领任务
4. 设置 `current_db_task`
5. 执行 `run_chat_turn()`
6. 成功则将任务标记为 `COMPLETED`
7. 失败则将任务标记为 `FAILED`，并将 Agent 状态置为 `FAILED`
8. 循环直到没有待处理任务

在 `finally` 中：

- 非失败态会回到 `IDLE`
- 若当前协程仍是 `self.consumer_task`，会清空句柄
- 退出时若又检测到待处理任务，会再次 `start_consumer_task()`

因此当前模型是：

- Scheduler 负责“投递数据库任务并唤起消费者”
- Agent 负责“串行消费自己的数据库任务”

并不是旧文档中所说的“Scheduler 把任务投递到 Agent 内存队列”。

### 4.4 Agent 执行一轮聊天

`run_chat_turn()` 会先按房间上下文同步未读消息，再调用 driver 执行本轮。

在 host-managed turn loop 下，Agent 通过工具完成本轮：

- `send_chat_msg`：向当前房间或其他房间写消息
- `finish_chat_turn`：显式结束当前轮次

其中：

- `send_chat_msg` 只写消息，不推进 turn
- 只有 `finish_chat_turn` 才会调用 `ChatRoom.finish_turn()` 交棒给下一位

## 5. ChatRoom 的状态推进

### 5.1 `add_message()`

房间收到消息后会：

1. 追加消息并发布 `ROOM_MSG_ADDED`
2. 如果消息发送者正是当前发言位，则将 `_current_turn_has_content=True`
3. 如果是插话，只记录消息，不推进 turn
4. 只要收到真实消息（非 `SYSTEM`），就清空 `_round_skipped_set`
5. 如果房间原本是 `IDLE`，则重置轮次并重新激活调度

### 5.2 `finish_turn()`

当前发言人结束本轮时：

1. 校验 `sender` 是否就是当前发言人
2. 若本轮没有发言内容，则把当前发言人加入 `_round_skipped_set`
3. 清空 `_current_turn_has_content`
4. 推进 `_turn_pos`
5. 如果跨轮则增加 `_turn_count`
6. 解析下一位可调度成员并按需发布事件

## 6. 停止条件与特殊成员策略

### 6.1 停止条件

停止逻辑统一收敛在 `ChatRoom._try_stop_scheduling()`，满足任一条件进入 `IDLE`：

1. `_turn_count >= _max_turns`
2. 所有 AI 成员都已进入 `_round_skipped_set`

这里“所有 AI 成员”不包含 `OPERATOR`。

### 6.2 Group 房间中的 Operator

当房间满足以下条件时：

- 当前发言位是 `OPERATOR`
- 房间类型是 `GROUP`
- 房间成员数大于 2

`_resolve_next_dispatchable_agent()` 内部的 `_should_auto_skip_agent_turn()` 会自动跳过 `OPERATOR`，将其加入 `_round_skipped_set` 并推进到下一位 AI 成员。

### 6.3 Private 房间中的 Operator

在 `PRIVATE` 房间中，`OPERATOR` 不会被自动跳过。

当前行为是：

- `_resolve_next_dispatchable_agent()` 检测到当前发言位是 `OPERATOR`（SpecialAgent）时返回 `None`
- 房间停在 `OPERATOR` 发言位，等待外部输入
- 前端 / API 通过 `roomController.RoomMessagesHandler.post()` 让 `OPERATOR` 写入消息
- 写入消息后由控制器显式调用 `room.finish_turn(SpecialAgent.OPERATOR.name)`，再把轮次交给 AI

也就是说，私聊中的 `OPERATOR` 回合是”等待人类输入”，调度器不会为此发布 `ROOM_AGENT_TURN`。

## 7. IDLE 唤醒

房间一旦进入 `IDLE`，任何新消息都会触发 `_update_turn_state_on_message()` 的唤醒逻辑：

1. 重置 `_turn_count`
2. 清空 `_round_skipped_set`
3. 清空 `_current_turn_has_content`
4. 将状态切回 `SCHEDULING`
5. 重新解析下一位并按需发布 `ROOM_AGENT_TURN`

因此唤醒逻辑依赖的是房间状态，而不是 `_turn_count` 是否已到上限。

## 8. 关键方法索引

### 8.1 `src/service/roomService.py`

- `ChatRoom.activate_scheduling`
- `ChatRoom.add_message`
- `ChatRoom.finish_turn`
- `ChatRoom._resolve_next_dispatchable_agent`
- `ChatRoom._should_auto_skip_agent_turn`
- `ChatRoom._publish_current_turn`
- `ChatRoom._try_stop_scheduling`
- `activate_rooms`

### 8.2 `src/service/schedulerService.py`

- `startup`
- `start_scheduling`
- `_on_agent_turn`
- `stop_team`

### 8.3 `src/service/agentService/agent.py`

- `Agent.start_consumer_task`
- `Agent.stop_consumer_task`
- `Agent.consume_task`
- `Agent.run_chat_turn`

### 8.4 `src/service/funcToolService/tools.py`

- `send_chat_msg`
- `finish_chat_turn`
