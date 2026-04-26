# Agent 任务生命周期

> 本文档描述 Agent 任务从创建到结束的完整生命周期，包括状态流转、消费流程、失败处理与恢复机制。

## 1. 整体架构

任务生命周期涉及 4 个组件协作：

```
schedulerService          AgentTaskConsumer          gtAgentTaskManager          AgentTurnRunner
    │                          │                          │                          │
    │ ── create_task ─────────>│                          │                          │
    │ ── start() ─────────────>│                          │                          │
    │                          │ ── consume() ───────────>│                          │
    │                          │    (claim/execute loop)   │                          │
    │                          │ ── _execute_task() ──────>│                          │
    │                          │                           │ ── run_chat_turn() ─────>│
```

| 组件 | 职责 |
|------|------|
| `schedulerService` | 监听房间轮次事件，创建任务记录（DB），触发消费 |
| `AgentTaskConsumer` | 消费循环主体：认领任务 → 执行 → 状态流转 → 失败恢复 |
| `gtAgentTaskManager` | DAL 层，所有任务状态的数据库读写 |
| `AgentTurnRunner` | 单轮 turn 执行，内部再按 step 推进消息同步、推理与工具调用 |

## 2. 状态定义

### 2.1 任务状态（AgentTaskStatus）

持久化在 `agent_tasks` 表中，驱动消费循环的判断逻辑。

| 状态 | 含义 | 数据库值 |
|------|------|----------|
| `PENDING` | 已创建，等待被认领 | `"PENDING"` |
| `RUNNING` | 已被消费者认领，正在执行 | `"RUNNING"` |
| `COMPLETED` | 执行成功 | `"COMPLETED"` |
| `FAILED` | 执行失败，需人工介入恢复 | `"FAILED"` |

### 2.2 Agent 状态（AgentStatus）

内存中的运行时状态，反映当前 Agent 是否正在工作。

| 状态 | 含义 |
|------|------|
| `ACTIVE` | 有消费协程在运行，正在处理任务 |
| `IDLE` | 空闲，无任务可消费 |
| `FAILED` | 最近一次任务执行失败，消费循环已停止 |

AgentStatus 通过 `messageBus` 发布 `AGENT_STATUS_CHANGED` 事件，经 WebSocket 推送给前端。

## 3. 任务状态流转图

```
                                   ┌──────────────────┐
                                   │ schedulerService  │
                                   │ create_task()     │
                                   └────────┬─────────┘
                                            │
                                            ▼
                               ┌────────────────────────┐
                               │       PENDING          │
                               │ (等待被消费者认领)       │
                               └────────────┬───────────┘
                                            │ transition_task_status()
                                            │ PENDING → RUNNING
                                            ▼
                               ┌────────────────────────┐
                               │       RUNNING          │
                               │ (正在执行 run_chat_turn)│
                               └──────┬───────────┬─────┘
                                      │           │
                              成功     │           │ 异常
                                      ▼           ▼
                         ┌──────────────┐   ┌──────────────┐
                         │  COMPLETED   │   │    FAILED    │
                         │ (归档，不再  │   │ (阻塞后续，  │
                         │  参与消费)   │   │  等待恢复)   │
                         └──────────────┘   └──────┬───────┘
                                                   │ resume_failed()
                                                   │ FAILED → RUNNING
                                                   ▼
                                          (重新进入 RUNNING)
```

### 3.1 合法流转路径

| 流转 | 触发方 | 方法 |
|------|--------|------|
| `PENDING → RUNNING` | `consume()` 循环 | `transition_task_status()` (原子 CAS) |
| `RUNNING → COMPLETED` | `_execute_task()` 成功 | `update_task_status()` |
| `RUNNING → FAILED` | `_execute_task()` 异常 | `update_task_status()` |
| `FAILED → RUNNING` | `resume_failed()` | `transition_task_status()` (原子 CAS) |

> **进程重启**时，遗留的 RUNNING 任务会被 `fail_running_tasks()` 强制标记为 FAILED。

## 4. 消费流程详解

### 4.1 任务创建与消费启动

```
schedulerService._on_agent_turn()
│
├── 1. 去重：has_pending_room_task(agent_id, room_id)
│        → 同一房间已有 PENDING 任务时跳过
│
├── 2. 写库：gtAgentTaskManager.create_task()
│        → 创建 PENDING 状态的任务记录
│
└── 3. 触发消费：agent.start_consumer_task()
          → AgentTaskConsumer.start()
              → asyncio.create_task(self.consume())
```

`start()` 保证幂等：如果已有消费协程在运行且未结束（`existing.done() is False`），直接返回。

### 4.2 消费循环（consume）

`consume()` 是一个 `while True` 循环，持续从数据库拉取并执行任务，直到以下退出条件之一满足：

```python
async def consume(initial_task=None):
    # 1. 状态切换：IDLE → ACTIVE
    status = ACTIVE
    _publish_status(ACTIVE)

    # 2. 消费循环
    while True:
        if claimed_task is None:
            task = get_first_unfinish_task()     # 查最早的 PENDING 或 FAILED
            if task is None:                     # 退出条件①：无未完成任务
                break
            if task.status != PENDING:           # 退出条件②：最早任务是 FAILED
                break
            claimed_task = transition(PENDING → RUNNING)
            if claimed_task is None:             # CAS 失败，重试
                continue

        ok = await _execute_task(claimed_task)
        if not ok:                               # 退出条件③：任务执行失败
            break

    # 3. 清理：ACTIVE → IDLE（或保持 FAILED）
    if status != FAILED:
        status = IDLE
        _publish_status(IDLE)

    # 4. 收尾检查：消费期间是否有新 PENDING 任务产生
    if has_consumable_task():
        start()  # 自动续起新消费协程
```

#### 退出条件总结

| # | 条件 | 含义 | 退出后 AgentStatus |
|---|------|------|-------------------|
| ① | `get_first_unfinish_task()` 返回 `None` | 所有任务已完成 | IDLE |
| ② | 最早未完成任务的 status 不是 PENDING | 队头有 FAILED 任务阻塞 | IDLE |
| ③ | `_execute_task()` 返回 `False` | 当前任务执行抛异常 | FAILED |

### 4.3 单任务执行（_execute_task）

```python
async def _execute_task(claimed_task, resumed):
    self.current_db_task = claimed_task        # 暴露当前任务引用

    try:
        await turn_runner.run_chat_turn(claimed_task, resumed=resumed)
    except Exception:
        # 失败路径
        update_task_status(task_id, FAILED, error_message=str(e))
        self.status = FAILED
        self.current_db_task = None
        _publish_status(FAILED)
        return False                            # 通知消费循环停止

    # 成功路径
    update_task_status(task_id, COMPLETED)
    self.current_db_task = None
    return True                                 # 继续消费下一个任务
```

关键设计：

- **失败即停**：任务执行异常后 Consumer 立即停止消费循环，不会跳过失败任务继续处理后续任务。
- **`current_db_task`**：在任务执行期间指向当前任务对象，执行结束后（无论成败）清空为 `None`。
- **`resumed` 参数**：标识该任务是否为恢复执行（影响 TurnRunner 的历史消息同步策略）。

## 5. 失败阻塞机制

### 5.1 FAILED 任务阻塞后续消费

`get_first_unfinish_task()` 查询 PENDING 和 FAILED 两种状态，按 `id ASC` 排序：

```python
# dal/db/gtAgentTaskManager.py
async def get_first_unfinish_task(agent_id):
    return GtAgentTask
        .where(status.in_([PENDING, FAILED]))
        .order_by(id.asc())
        .first()
```

这意味着：

- 如果最早的未完成任务是 FAILED，`consume()` 会在退出条件②处停止
- 后续 PENDING 任务**不会被跳过执行**，必须先恢复 FAILED 任务
- 这保证了**任务的严格顺序性**

### 5.2 新任务到达时的行为

当 Agent 处于 FAILED 状态时，新任务仍可正常创建：

```
schedulerService.create_task()  →  PENDING 任务写入 DB
agent.start_consumer_task()     →  Consumer.start()
                                    └── consume()
                                         └── get_first_unfinish_task()
                                              → 返回 FAILED 任务（队头）
                                              → status != PENDING → break
                                              → 消费循环立即退出
```

结果：新创建的 PENDING 任务排在 FAILED 任务之后，无法被消费。Agent 保持 FAILED 状态，等待人工恢复。

### 5.3 `has_consumable_task()` 的判断

```python
async def has_consumable_task(agent_id):
    first = await get_first_unfinish_task(agent_id)
    return first is not None and first.status == PENDING
```

仅当**最早的**未完成任务是 PENDING 时返回 True。有 FAILED 队头时返回 False，不会触发自动续起。

## 6. 恢复机制

### 6.1 人工恢复（resume_failed）

用户通过 `POST /agents/<id>/resume.json` 触发：

```
Controller (校验 agent.status == FAILED)
└── Agent.resume_failed()
    └── AgentTaskConsumer.resume_failed()
        │
        ├── 1. 查询：get_first_unfinish_task()
        │       → 断言结果非 None 且 status == FAILED
        │
        ├── 2. 状态迁移：transition_task_status(FAILED → RUNNING)
        │       → 原子 CAS，防止并发恢复冲突
        │
        ├── 3. 更新内存状态：status = ACTIVE
        │       → _publish_status(ACTIVE)
        │
        └── 4. 启动消费：start(initial_task=resumed_task)
                → 传入已处于 RUNNING 状态的任务，跳过认领步骤
                → consume() 循环从 _execute_task() 开始
```

### 6.2 进程重启恢复

```
core.restore_state()
│
├── 1. 加载历史消息：load_agent_history_message() → inject 到 TurnRunner
│
├── 2. 清理遗留：fail_running_tasks(agent_id)
│       → 将所有 RUNNING 任务标记为 FAILED
│       → error_message = "task interrupted by process restart"
│
└── 3. 设置内存状态：
        first_task = get_first_unfinish_task()
        if first_task.status == FAILED:
            consumer.status = FAILED
        else:
            consumer.status = IDLE
```

进程重启后不会自动重试失败任务，需要用户手动触发 `resume`。

## 7. 并发安全

### 7.1 任务认领的原子性

`transition_task_status()` 使用数据库层面的 CAS（Compare-And-Swap）：

```python
async def transition_task_status(task_id, from_status, to_status):
    result = GtAgentTask
        .update(status=to_status)
        .where(id == task_id, status == from_status)
        .execute()
    if result == 0:    # 状态已被其他消费者改变
        return None
    return GtAgentTask.get(task_id)
```

- 如果 CAS 返回 None，`consume()` 会 `continue` 重新查询
- 保证同一任务不会被两个消费协程同时执行

### 7.2 消费协程的幂等启动

```python
def start(initial_task=None):
    existing = self._aio_consumer_task
    if existing is not None and not existing.done():
        return                           # 幂等：已有运行中协程，跳过
    self._aio_consumer_task = asyncio.create_task(self.consume(...))
```

多次调用 `start()` 安全：只有在没有活跃消费协程时才会创建新的。

### 7.3 重复消费协程检测

```python
async def consume():
    current = asyncio.current_task()
    if self._aio_consumer_task not in (None, current):
        if existing.done() is False:
            logger.warning("检测到重复启动的消费协程")
```

防御性检查：在 `start()` 的幂等保护之外，`consume()` 入口再次校验。

## 8. 事件通知链路

```
AgentTaskConsumer._publish_status()
    │
    ▼
messageBus.publish(AGENT_STATUS_CHANGED, gt_agent, status)
    │
    ▼
wsController._on_event()
    │ payload["event"] = "agent_status"
    ▼
WebSocket → 前端 UI 更新
```

状态变更通知的触发时机：

| 时机 | 状态变化 | 触发位置 |
|------|----------|----------|
| 消费循环启动 | → ACTIVE | `consume()` 入口 |
| 任务执行失败 | → FAILED | `_execute_task()` 异常处理 |
| 消费循环正常结束 | → IDLE | `consume()` 清理逻辑 |
| 失败恢复 | → ACTIVE | `resume_failed()` |

## 9. 消费自动续起

消费循环结束后，会检查是否有新任务在消费期间到达：

```python
# consume() 尾部
if self._aio_consumer_task is current_consumer:
    self._aio_consumer_task = None              # 先清空引用
    if self.status != FAILED:
        if has_consumable_task():                # 有可消费的 PENDING 任务
            self.start()                        # 启动新消费协程
```

这处理了以下场景：

1. Consumer 正在执行任务 A
2. 此时 schedulerService 创建了任务 B，调用 `start()`，因已有协程在运行而跳过
3. 任务 A 完成后，`consume()` 正常退出
4. 收尾检查发现任务 B 存在，自动续起新消费协程

## 10. 方法速查

### AgentTaskConsumer

| 方法 | 说明 |
|------|------|
| `start(initial_task?)` | 幂等启动消费协程 |
| `stop()` | 取消消费协程 |
| `consume(initial_task?)` | 消费循环主体 |
| `_execute_task(task, resumed)` | 执行单个任务，返回成功/失败 |
| `resume_failed()` | 恢复最早的 FAILED 任务 |
| `_publish_status(status)` | 通过 messageBus 广播状态变更 |

### gtAgentTaskManager

| 方法 | 说明 |
|------|------|
| `create_task(agent_id, type, data)` | 创建 PENDING 任务 |
| `get_first_unfinish_task(agent_id)` | 获取最早的 PENDING/FAILED 任务 |
| `has_pending_room_task(agent_id, room_id)` | 检查同房间是否有 PENDING 任务（去重） |
| `has_consumable_task(agent_id)` | 队头是否为可消费的 PENDING 任务 |
| `transition_task_status(id, from, to)` | 原子 CAS 迁移状态 |
| `update_task_status(id, status, error?)` | 直接更新状态 |
| `get_running_tasks(agent_id)` | 获取 RUNNING 任务（进程重启恢复用） |
