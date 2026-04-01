# Agent 调度逻辑说明文档

本文档描述当前版本（`src/service/roomService.py` + `src/service/schedulerService.py`）的回合调度实现。

## 1. 核心角色

- **ChatRoom（房间状态机）**：维护 turn/round 相关运行态并决定“下一位该谁发言”。
- **Scheduler（事件分发器）**：订阅 `ROOM_MEMBER_TURN`，把任务投递到对应 Agent 队列并拉起消费协程。
- **Agent Driver（执行器）**：执行本轮推理与工具调用，最终调用 `finish_chat_turn` 交回控制权。

## 2. 关键状态（ChatRoom）

- `_turn_pos`：当前发言位索引（指向 `_member_names`）。
- `_turn_count`：已完成整轮数（所有成员完整走一圈计 1）。
- `_current_turn_has_content`：当前发言位是否已产出真实消息。
- `_round_skipped_set`：自上次真实消息以来，已“跳过发言”的成员集合。
- `_state`：`INIT / SCHEDULING / IDLE`。

## 3. 启动与入口

### 3.1 启动阶段
`backend_main` 在恢复状态后调用 `schedulerService.start_scheduling()`：

1. `schedulerService.start_scheduling()`
2. `roomService.activate_rooms()`
3. `ChatRoom.activate_scheduling()`
4. 解析下一位可调度成员并发布 `ROOM_MEMBER_TURN`

### 3.2 热更新阶段
`teamService.hot_reload_team()` 在刷新房间后同样调用 `schedulerService.start_scheduling(team_name)`，复用同一入口。

## 4. 单个 Turn 的生命周期

### 4.1 Start
1. `ChatRoom` 发布 `ROOM_MEMBER_TURN(member_name, room_id, team_name, ...)`
2. `schedulerService._on_member_turn` 入队 `GtCoreRoomMessageEvent(room_id)`
3. `Agent.consume_task` 消费队列并执行 `run_chat_turn`

注：当 `member_name` 是 `OPERATOR` 时，调度器不会拉起 AI 任务（仅记录等待状态）。

### 4.2 Action
- Agent 可多次调用 `send_chat_msg`
- 每次消息只更新 `_current_turn_has_content=True`，不会自动推进 turn

### 4.3 Finish
- Agent 必须调用 `finish_chat_turn`
- `ChatRoom.finish_turn` 会：
  1. 校验当前发言人
  2. 如本轮无内容则加入 `_round_skipped_set`
  3. 推进到下一发言位
  4. 解析下一位可调度成员并发布事件（或停止）

## 5. 停止与跳过策略

### 5.1 停止条件（统一收敛）
停止逻辑集中在 `ChatRoom._try_stop_scheduling()`，满足任一条件进入 `IDLE`：

1. `_turn_count >= _max_turns`
2. 所有 AI 成员都在 `_round_skipped_set` 中（滑动窗口）

### 5.2 Operator 自动跳过
在群聊且成员数 > 2 时，若当前发言位为 `OPERATOR`，会自动跳过到下一位 AI（不等待人类输入）。

### 5.3 真实消息会重置跳过窗口
只要收到非系统消息，`_round_skipped_set` 会清空，重新开始“全员跳过”判定窗口。

## 6. IDLE 唤醒

房间处于 `IDLE` 时，任何新消息都会触发唤醒：

1. 重置 `_turn_count`、`_round_skipped_set`、`_current_turn_has_content`
2. 切回 `SCHEDULING`
3. 重新解析并发布下一位 `ROOM_MEMBER_TURN`

## 7. 关键方法索引

- `src/service/roomService.py`
  - `ChatRoom.finish_turn`
  - `ChatRoom._go_next_turn`
  - `ChatRoom._try_stop_scheduling`
  - `ChatRoom._resolve_next_dispatchable_member`
  - `ChatRoom._publish_current_turn`（纯发布）
- `src/service/schedulerService.py`
  - `start_scheduling`
  - `_on_member_turn`
- `src/service/funcToolService/tools.py`
  - `send_chat_msg`
  - `finish_chat_turn`
