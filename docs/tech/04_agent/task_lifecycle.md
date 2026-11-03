# Agent 任务生命周期

> 本文档描述 Agent 任务从创建到结束的完整生命周期，包括状态流转、消费流程、失败处理与恢复机制。
>
> **命名说明（0.5.0 起）**：调度侧原 `GtScheculeTask` / `schecule_tasks` / `gtScheculeTaskManager` /
> `AgentTaskType` / `AgentTaskStatus` / `AgentTaskConsumer` 已统一改名为 `GtScheduleJob` / `schedule_jobs` /
> `gtScheduleJobManager` / `ScheduleJobType` / `ScheduleJobStatus` / `JobConsumer`。
> **协作任务侧（`GtAgentTask` / `gtAgentTaskManager` / `taskService` / `TaskStatus` / `agent_task_id`）保持不变**，
> 文中出现「协作任务」时指的是后者。详见 `docs/tech/04_agent/schedule_job_rename.md`。

## 1. 整体架构

任务生命周期涉及 4 个组件协作：

```
schedulerService          JobConsumer          gtScheduleJobManager          AgentTurnRunner
    │                          │                          │                          │
    │ ── create_job ──────────>│                          │                          │
    │ ── start() ─────────────>│                          │                          │
    │                          │ ── consume() ───────────>│                          │
    │                          │    (claim/execute loop)   │                          │
    │                          │ ── transition/update ───>│                          │
    │                          │                           │ ── run_task_turn() ─────>│
```

| 组件 | 职责 |
|------|------|
| `schedulerService` | 监听房间轮次事件，创建任务记录（DB），触发消费 |
| `JobConsumer` | 消费循环主体：认领任务 → 执行 → 状态流转 → 失败恢复 |
| `gtScheduleJobManager` | DAL 层，所有任务状态的数据库读写 |
| `AgentTurnRunner` | 单轮 turn 执行，内部再按 step 推进消息同步、推理与工具调用 |

## 2. 状态定义

### 2.1 任务状态（ScheduleJobStatus）

持久化在 `schedule_jobs` 表中，驱动消费循环的判断逻辑。

| 状态 | 含义 | 数据库值 |
|------|------|----------|
| `PENDING` | 已创建，等待被认领 | `"PENDING"` |
| `RUNNING` | 已被消费者认领，正在执行 | `"RUNNING"` |
| `COMPLETED` | 执行成功 | `"COMPLETED"` |
| `FAILED` | 执行失败，可被后续 `consume()` 重跑 | `"FAILED"` |
| `CANCELLED` | 被操作者人工停止 | `"CANCELLED"` |

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
                                   │ create_job()      │
                                   └────────┬─────────┘
                                            │
                                            ▼
                               ┌────────────────────────┐
                               │       PENDING          │
                               │ (等待被消费者认领)       │
                               └────────────┬───────────┘
                                            │ transition_job_status()
                                            │ PENDING → RUNNING
                                            ▼
                               ┌────────────────────────┐
                               │       RUNNING          │
                               │ (正在执行 run_task_turn)│
                               └──────┬───────────┬─────┘
                                      │           │
                              成功     │           │ 异常
                                      ▼           ▼
                         ┌──────────────┐   ┌──────────────┐
                         │  COMPLETED   │   │    FAILED    │
                         │ (归档，不再  │   │ (可被下一轮  │
                         │  参与消费)   │   │  consume 重跑)│
                         └──────────────┘   └──────┬───────┘
                                                   │ transition_job_status()
                                                   │ FAILED → RUNNING
                                                   ▼
                                          (重新进入 RUNNING)
```

### 3.1 合法流转路径

| 流转 | 触发方 | 方法 |
|------|--------|------|
| `PENDING → RUNNING` | `consume()` 循环 | `transition_job_status()` (原子 CAS) |
| `RUNNING → COMPLETED` | `consume()` 成功分支 | `update_job_status()` |
| `RUNNING → FAILED` | `consume()` 异常分支 | `update_job_status()` |
| `RUNNING → CANCELLED` | 人工停止 turn | `update_job_status()` |
| `FAILED → RUNNING` | `consume()` 循环（自动重跑） | `transition_job_status()` (原子 CAS) |

> **进程重启**时，遗留的 RUNNING job 会被 `fail_running_tasks()` 强制标记为 FAILED。

## 4. 消费流程详解

### 4.1 任务创建与消费启动

```
schedulerService._on_agent_turn()
│
├── 1. 去重：has_pending_room_job(agent_id, room_id)
│        → 同一房间已有 PENDING 任务时跳过
│
├── 2. 写库：gtScheduleJobManager.create_job()
│        → 创建 PENDING 状态的任务记录
│
└── 3. 触发消费：agent.start_consumer_task()
          → JobConsumer.start()
              → asyncio.create_task(self.consume())
```

`start()` 保证幂等：如果已有消费协程在运行且未结束（`existing.done() is False`），直接返回。

### 4.2 消费循环（consume）

`consume()` 是一个 `while True` 循环，持续从数据库拉取并执行 job，直到以下退出条件之一满足：

```python
async def consume():
    # 1. 状态切换：IDLE → ACTIVE
    await self._set_status(AgentStatus.ACTIVE)

    # 2. 消费循环
    while True:
        job = await gtScheduleJobManager.get_first_unfinished_job(agent_id)
        if job is None:                          # 退出条件①：无未完成任务
            break
        if job.status not in (PENDING, RUNNING, FAILED):   # 退出条件②：队头不可消费
            break
        claimed_job = (await gtScheduleJobManager.transition_job_status(
            job.id, job.status, ScheduleJobStatus.RUNNING) if job.status in (PENDING, FAILED) else job)
        if claimed_job is None:                  # CAS 失败，重试
            continue

        try:
            await self._turn_runner.run_task_turn(claimed_job)
        except Exception:
            await gtScheduleJobManager.update_job_status(claimed_job.id, FAILED, error_message=str(e))
            await self._set_status(AgentStatus.FAILED, str(e))
            break                                # 退出条件③：任务执行失败
        await gtScheduleJobManager.update_job_status(claimed_job.id, COMPLETED)

    # 3. 清理：ACTIVE → IDLE（或保持 FAILED）
    if self.status != AgentStatus.FAILED:
        await self._set_status(AgentStatus.IDLE)

    # 4. 收尾检查：消费期间是否有新 PENDING 任务产生
    if await gtScheduleJobManager.has_consumable_job(agent_id):
        self.start()                             # 自动续起新消费协程
```

#### 退出条件总结

| # | 条件 | 含义 | 退出后 AgentStatus |
|---|------|------|-------------------|
| ① | `get_first_unfinished_job()` 返回 `None` | 所有任务已完成 | IDLE |
| ② | 队头 job 的 status 不在 PENDING / RUNNING / FAILED | 出现不可消费状态（防御性判断，查询已过滤） | IDLE |
| ③ | `run_task_turn()` 抛异常 | 当前 job 执行失败 | FAILED |

> **取队头规则**：`get_first_unfinished_job()` 按 `job_type ASC, id ASC` 排序（`ROOM_MESSAGE` 优先于 `TODO_TASK`），
> 因此只要有房间消息待处理，Agent 会先回消息，消息处理完才轮到协作任务。

### 4.3 单 job 执行（consume 循环内联）

```python
# consume() 循环体内（_execute_task 已内联，见 commit 3ebf86b0）
try:
    await self._turn_runner.run_task_turn(claimed_job)   # TurnRunner 内部维护 _current_job
except asyncio.CancelledError:
    if not self._cancel_requested:
        raise                                            # 非人工停止，穿透
    await self._turn_runner.handle_cancel_turn()
    await gtScheduleJobManager.update_job_status(claimed_job.id, CANCELLED,
                                                error_message="cancelled by user")
    break
except Exception as e:
    # 失败路径
    await gtScheduleJobManager.update_job_status(claimed_job.id, FAILED, error_message=str(e))
    await self._set_status(AgentStatus.FAILED, str(e))
    break                                                # 通知消费循环停止

# 成功路径
await gtScheduleJobManager.update_job_status(claimed_job.id, COMPLETED)
```

关键设计：

- **失败即停**：任务执行异常后 Consumer 立即停止消费循环，不会跳过失败任务继续处理后续任务。
- **`_current_job`**：由 `AgentTurnRunner` 在 `run_task_turn` 执行期间持有，turn 结束（无论成败）清空为 `None`。`JobConsumer` 自身不再持有当前 job 引用（原 `current_db_task` 已随 `3713f05a` 移除）。
- **`resumed` 参数**：已随 `3713f05a` 移除；FAILED job 的重跑由 `consume()` 里 `FAILED → RUNNING` 的 CAS 直接覆盖。

## 5. FAILED job 的处理

### 5.1 FAILED 会被自动重跑（不再阻塞）

`get_first_unfinished_job()` 查询 PENDING / RUNNING / FAILED 三种状态，按 `job_type ASC, id ASC` 排序：

```python
# dal/db/gtScheduleJobManager.py
async def get_first_unfinished_job(agent_id):
    return await (
        GtScheduleJob
        .select()
        .where(
            GtScheduleJob.agent_id == agent_id,
            GtScheduleJob.status.in_([PENDING, RUNNING, FAILED]),
        )
        .order_by(GtScheduleJob.job_type.asc(), GtScheduleJob.id.asc())
        .aio_first()
    )
```

`consume()` 拿到队头 job 后：

- `PENDING` / `FAILED` → `transition_job_status(id, 当前状态, RUNNING)` 原子认领，然后执行
- `RUNNING` → 直接沿用（进程内续跑场景）

也就是说 **FAILED job 是「可重跑」的，不会永久阻塞队列**。Agent 是否继续消费，取决于 `AgentStatus` 和
`has_consumable_job()`，而不是「队头是否 FAILED」。

### 5.2 新 job 到达时的行为

Agent 处于 FAILED 状态（上一次执行抛异常）时，新 job 仍可正常创建并落库：

```
schedulerService.create_job()   →  PENDING job 写入 DB
agent.start_consumer_task()     →  JobConsumer.start()
                                    └── consume()
                                         └── get_first_unfinished_job()
                                              → 队头 job 可认领 → 继续执行
```

若 Agent 处于 CLOSED 状态，`Agent.start_consumer_task()` 会直接忽略（打日志，不启动消费）。

### 5.3 `has_consumable_job()` 的判断

```python
async def has_consumable_job(agent_id):
    first_job = await get_first_unfinished_job(agent_id)
    return first_job is not None and first_job.status == ScheduleJobStatus.PENDING
```

仅当**队头** job 是 PENDING 时返回 True，用于 `consume()` 收尾时的「自动续起」判断。
若队头是 RUNNING（别的协程在跑）或 FAILED（需人工触发重跑语义），则不自动续起。

## 6. 恢复机制

### 6.1 人工恢复（resume_failed）

用户通过 `POST /agents/<id>/resume.json` 触发：

```
AgentResumeHandler.post()
└── 前置断言：agent.status == FAILED
    └── Agent.start_consumer_task()
        └── JobConsumer.start()
            └── consume() 重新进入循环
                ├── 查询：get_first_unfinished_job(agent_id)
                │       → 队头是 FAILED job
                ├── 状态迁移：transition_job_status(job.id, FAILED → RUNNING)
                │       → 原子 CAS，防止并发恢复冲突
                └── 继续执行该 job（run_task_turn）
```

> `resume_failed()` 这一独立入口已不再存在（`3ebf86b0` 把 `_execute_task` 内联进 `consume` 后取消）；
> 恢复动作等价于「把队头 FAILED job CAS 成 RUNNING 后重跑」。

### 6.2 进程重启恢复

```
core.restore_state()
│
├── 1. 加载历史消息：load_agent_history_message() → inject 到 TurnRunner
│
├── 2. 清理遗留：fail_running_tasks(agent_id)
│       → 将所有 RUNNING job 标记为 FAILED
│       → error_message = "task interrupted by process restart"
│
└── 3. 设置内存状态：
        first_job = get_first_unfinished_job(agent_id)
        if first_job.status == FAILED:
            consumer.status = FAILED
        else:
            consumer.status = IDLE
```

进程重启后不会自动重试失败 job，需要用户手动触发 `POST /agents/<id>/resume.json`。

## 7. 并发安全

### 7.1 job 认领的原子性

`transition_job_status()` 使用数据库层面的 CAS（Compare-And-Swap）：

```python
async def transition_job_status(job_id, from_status, to_status):
    result = GtScheduleJob
        .update(status=to_status)
        .where(id == job_id, status == from_status)
        .execute()
    if result == 0:    # 状态已被其他消费者改变
        return None
    return GtScheduleJob.get(job_id)
```

- 如果 CAS 返回 None，`consume()` 会 `continue` 重新查询
- 保证同一 job 不会被两个消费协程同时执行

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
JobConsumer._publish_status()
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
| 消费循环启动 | → ACTIVE | `consume()` 入口 `_set_status(ACTIVE)` |
| 任务执行失败 | → FAILED | `consume()` 内 `except Exception` 分支 |
| 消费循环正常结束 | → IDLE | `consume()` 清理逻辑 |
| 失败恢复 | → ACTIVE | `AgentResumeHandler` → `start()` → `consume()` |

## 9. 消费自动续起

消费循环结束后，会检查是否有新任务在消费期间到达：

```python
# consume() 尾部
if self._aio_consumer_task is current_consumer:
    self._aio_consumer_task = None              # 先清空引用
    if self.status != FAILED:
        if has_consumable_job():                # 有可消费的 PENDING 任务
            self.start()                        # 启动新消费协程
```

这处理了以下场景：

1. Consumer 正在执行任务 A
2. 此时 schedulerService 创建了任务 B，调用 `start()`，因已有协程在运行而跳过
3. 任务 A 完成后，`consume()` 正常退出
4. 收尾检查发现任务 B 存在，自动续起新消费协程

## 10. 方法速查

### JobConsumer

| 方法 | 说明 |
|------|------|
| `start()` | 幂等启动消费协程 |
| `stop()` | 取消消费协程 |
| `consume()` | 消费循环主体（认领 → 执行 → 状态流转） |
| `cancel_current_turn()` | 人工停止当前 turn |
| `_set_status(status, error?)` | 更新运行时状态 + 广播 `AGENT_STATUS_CHANGED` + 记录活动 |

### gtScheduleJobManager

| 方法 | 说明 |
|------|------|
| `create_job(agent_id, type, data)` | 创建 PENDING 调度任务 |
| `get_first_unfinished_job(agent_id)` | 获取最早的 PENDING/FAILED 任务 |
| `has_pending_room_job(agent_id, room_id)` | 检查同房间是否有 PENDING 任务（去重） |
| `has_pending_collaboration_job(agent_id, task_id)` | 检查协作任务是否已有 PENDING 调度 |
| `has_consumable_job(agent_id)` | 队头是否为可消费的 PENDING 任务 |
| `transition_job_status(id, from, to)` | 原子 CAS 迁移状态 |
| `update_job_status(id, status, error?)` | 直接更新状态 |
| `get_running_jobs(agent_id)` | 获取 RUNNING 任务（进程重启恢复用） |
| `get_job(job_id)` | 按 ID 取单个调度任务 |
| `delete_jobs_by_team(team_id)` | 清空团队数据时批量删除 |
