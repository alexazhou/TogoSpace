# 调度任务重命名（schedule task → job）

> **生效文档（当前代码以此为准）。** 0.5.0 起，Agent 调度侧的命名统一为 `job`；协作任务侧保持 `task` 不动。
> 本文只描述**改名后**的对外符号与迁移方式，实施细节与决策过程见
> `shared/调度任务重命名-实施报告-20261103.md` 与 `调度任务重命名方案.md`。

## 1. 为什么要区分 job / task

本仓有两套长得很像的「任务」概念：

| 概念 | 含义 | 归属 |
|------|------|------|
| **调度 job**（本次改名对象） | scheduler 投递给 Agent 的一次执行单元，Agent 消费它就跑一个 turn | `GtScheduleJob` / `gtScheduleJobManager` / `AgentJobConsumer` |
| **协作 task**（保持不变） | Agent 之间结构化的协作任务，有标题/描述/执行人/验收人/状态机 | `GtAgentTask` / `gtAgentTaskManager` / `taskService` / `TaskStatus` |

旧命名里两者都叫 task（`GtScheculeTask` 甚至带个拼写错的 `schecule`），读代码时极易混淆：
`task.task_type` 到底指调度类型还是协作任务类型？`create_task` 是建调度记录还是建协作任务？
本次改名把调度侧彻底切成 `job`，让「job = 执行单元」「task = 协作任务」在全仓一眼可辨。

## 2. 改名对照表（调度侧，全部已改）

### 2.1 数据层

| 旧 | 新 |
|----|----|
| 表 `schecule_tasks` | 表 `schedule_jobs` |
| 列 `task_type` | 列 `job_type` |
| 索引 `idx_schecule_tasks_agent_status` | 索引 `idx_schedule_jobs_agent_status` |
| 模型文件 `model/dbModel/gtScheculeTask.py` | `model/dbModel/gtScheduleJob.py` |
| 模型类 `GtScheculeTask` | `GtScheduleJob` |
| Manager 文件 `dal/db/gtScheculeTaskManager.py` | `dal/db/gtScheduleJobManager.py` |
| Manager 单例 `gtScheculeTaskManager` | `gtScheduleJobManager` |
| 枚举 `AgentTaskType` | `AgentJobType` |
| 枚举 `AgentTaskStatus` | `AgentJobStatus` |

**列 `task_data` 保留原名**（决策 §3.7）：它是通用 payload（`{"room_id": …}` / `{"agent_task_id": …}`），
不同 job 类型里语义不同，叫 `job_data` 反而不如 `task_data` 直白，且改名会牵动 22 处读写。

### 2.2 Manager 方法（10 个）

| 旧 | 新 |
|----|----|
| `create_task` | `create_job` |
| `get_task` | `get_job` |
| `has_pending_room_task` | `has_pending_room_job` |
| `get_first_unfinish_task` | `get_first_unfinished_job` |
| `has_consumable_task` | `has_consumable_job` |
| `has_pending_collaboration_task` | `has_pending_collaboration_job` |
| `transition_task_status` | `transition_job_status` |
| `update_task_status` | `update_job_status` |
| `get_running_tasks` | `get_running_jobs` |
| `delete_tasks_by_team` | `delete_jobs_by_team` |

### 2.3 服务层

| 旧 | 新 |
|----|----|
| 文件 `service/agentService/agentTaskConsumer.py` | `service/agentService/agentJobConsumer.py` |
| 类 `AgentTaskConsumer` | `AgentJobConsumer` |
| `Agent.task_consumer` | `Agent.job_consumer` |
| `AgentTaskConsumer` 内 `current_db_task` | 已移除（`3713f05a`），现由 `AgentTurnRunner._current_job` 承担 |
| `ToolCallContext.schedule_task` | `ToolCallContext.schedule_job` |
| `AgentTurnRunner._current_task` | `AgentTurnRunner._current_job` |

### 2.4 明确**不改**的符号

这些也是 `task`，但语义正确、或属外部契约、或属 Python/工具标准 API，一律不动：

- `GtAgentTask` / `gtAgentTaskManager` / `taskService` / `TaskStatus` / `agent_task_id`（协作任务侧）
- 工具名 `create_task` / `get_task` / `list_tasks`，以及 `AgentTasksHandler`
- `asyncio.create_task`（标准库）
- `run_task_turn` / `_run_turn_loop` / `handle_cancel_turn`（运行时保留 turn 语义）
- 枚举**值名** `TODO_TASK`（保留历史叫法 ⇒ 零存量数据 UPDATE）

## 3. 迁移

### 3.1 迁移脚本

`assets/migrate/0018_rename_schecule_tasks_to_schedule_jobs.sql`，一句话概括：

```sql
BEGIN;
ALTER TABLE schecule_tasks RENAME TO schedule_jobs;
ALTER TABLE schedule_jobs RENAME COLUMN task_type TO job_type;
DROP INDEX IF EXISTS idx_schecule_tasks_agent_status;
CREATE INDEX IF NOT EXISTS idx_schedule_jobs_agent_status ON schedule_jobs (agent_id, status);
COMMIT;
```

要点：

- **显式 `BEGIN;` / `COMMIT;`**。SQLite 的 `ALTER TABLE RENAME` 在部分驱动下会隐式提交，
  包在显式事务里才能保证「中途失败 → 整体回滚」。`tests/unit/test_migration_0018.py` 钉住了这两条语句
  （`BEGIN;` 必须是首条、`COMMIT;` 必须是末条、各恰好一条）与回滚行为。
- **无数据 UPDATE**。枚举值名 `TODO_TASK` 未变，`job_type` 列里存的仍是 `ROOM_MESSAGE` / `TODO_TASK`，
  迁移前后条数与分布完全一致。
- **不建兼容视图 / 触发器**（决策 §6.4）。降级需回滚迁移，不支持「旧代码读新库」。

### 3.2 发版注意

- 这是**破坏性变更**：旧版本代码无法读新库（找不到 `schecule_tasks`）。发版说明须写明。
- 迁移前请备份 `data/data.db`（应用启动时的自动备份即可）。
- 版本号 0.5.0。

## 4. 验证口径

改名类重构的验收靠「归零 + 反向校验」两道：

**归零**（`src/` 与 `tests/` 中须全为 0）：

```
schecule / GtScheculeTask / gtScheculeTaskManager / AgentTaskType / AgentTaskStatus
AgentTaskConsumer / agentTaskConsumer / task_consumer / schedule_task / .task_type
```

> 例外：`tests/unit/test_migration_0018.py` 里必须保留旧表名 `schecule_tasks`（它测的就是迁移本身）。

**反向校验**（数量必须不变，防批量替换误伤）：

| 项 | 期望 |
|----|------|
| `asyncio.create_task` | 7 处，一处都不能少 |
| 工具名 `"create_task"` | 仍在 `toolRegistry` / `funcToolService.core` |
| `tools.py` 的 `def create_task` | 仍在 |
| `class AgentTasksHandler` | 仍在 `agentController.py` |
| `gtAgentTaskManager.get_task` / `delete_tasks_by_team` | 仍在 |
| `asyncio.create_job` | 0（说明没把标准库 API 改坏） |

## 5. 相关文档

- `docs/tech/04_agent/task_lifecycle.md` — job 生命周期（状态流转 / 消费循环 / 恢复）
- `docs/tech/04_agent/scheduling_logic.md` — 调度链路与消费细节
- `docs/tech/04_agent/task_driven_wakeup_design.md` — 任务驱动唤醒设计
- `docs/tech/04_agent/driver_architecture.md` — driver 与 TurnRunner 的分工
- `docs/tech/04_agent/turn_runner_as_driver_host.md` — **历史方案快照**，符号已过时，仅供追溯
