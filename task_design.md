# Agent Task 系统设计文档

> [!WARNING]
> **本文档为早期草稿（v0.8，2026-05-22），口径已全部过时，请勿据此实现。** 正式口径见
> [V22 设计](./docs/design/2026-05-23-agent-task-system/)（已被取代）与
> [V27 / Agent 任务系统 v2](./docs/design/2026-10-25-agent-task-system-v2/)（现行）。
> 主要差异：`manager_id` → `reviewer_id`（任务域）；`update_task(status, result, block_reason)` → `update_task(task_id, new_status=None, note='')`；
> `get_task` → `get_task_detail`；新增 `list_task_events` 与事件时间线 `agent_task_events`；新增 `PENDING` / `CANCELLED` 状态与系统自动推进 / 认领置位。

> 版本：v0.8（草稿）
> 作者：小马哥
> 最后更新：2026-05-22

---

## 一、背景与目标

当前 Agent 团队的任务协作完全依赖聊天消息，缺乏结构化的任务跟踪机制，存在以下问题：
- 任务状态隐藏在消息流中，难以全局追踪
- 任务分配、进展、完成结果无法持久化查询
- 无法表达任务间的依赖关系

**目标：** 为 Agent 提供一套轻量级的任务管理工具，支持任务的创建、分配、状态跟踪、依赖管理和验收流程。

**范围：** 仅 Agent 工具层，不含前端界面。

---

## 二、数据模型

### tasks 表

```sql
CREATE TABLE tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER NOT NULL,
    title          TEXT    NOT NULL,
    description    TEXT    DEFAULT '',
    assignee_id    INTEGER NOT NULL,     -- 执行人：Agent ID（OPERATOR 用特殊 ID 表示）
    creator_id     INTEGER NOT NULL,     -- 创建人：Agent ID（OPERATOR 用特殊 ID 表示）
    manager_id     INTEGER,              -- 管理人：Agent ID，负责验收和管理此任务，可选
    status         TEXT    NOT NULL DEFAULT 'TODO',
    priority       TEXT    NOT NULL DEFAULT 'NORMAL',
    parent_id      INTEGER,              -- 父任务 ID，支持子任务拆解
    depends_on     TEXT    DEFAULT '[]', -- JSON 数组，依赖的 task_id 列表
    room_id        INTEGER,              -- 关联房间（可选，便于溯源）
    result         TEXT    DEFAULT '',   -- 完成时的交付摘要
    block_reason   TEXT    DEFAULT '',   -- ON_HOLD 时的搁置原因
    created_at     DATETIME NOT NULL,
    updated_at     DATETIME NOT NULL
);
```

### manager 字段说明

`manager_id` 存储管理人的 Agent ID，职责包括：
- **验收（Review）**：在 assignee 提交完成后，决定是通过（→ DONE）还是打回（→ IN_PROGRESS）
- **任务管理**：可修改任务优先级、描述等属性
- **取消任务**：可将任务置为 CANCELLED

通常由创建任务的上级 Agent（如 PM）担任。若未指定 manager_id，则任务无需验收，assignee 可直接将状态推进到 DONE。

---

## 三、任务状态与状态机

### 状态定义

| 状态 | 说明 |
|------|------|
| `TODO` | 待处理，初始状态 |
| `PENDING` | 等待依赖任务完成（任务未开始，前置依赖尚未满足） |
| `IN_PROGRESS` | 执行中 |
| `ON_HOLD` | 执行中途被搁置（非依赖原因，如需外部信息或决策） |
| `REVIEWING` | 待验收（assignee 提交完成，等待 manager 审核） |
| `DONE` | 已完成 |
| `CANCELLED` | 已取消 |

### 状态流转规则

```
TODO ──────────────→ IN_PROGRESS
TODO ──────────────→ PENDING          (存在未完成的依赖时)
TODO ──────────────→ CANCELLED

PENDING ───────────→ IN_PROGRESS      (依赖全部 DONE 后)
PENDING ───────────→ CANCELLED

IN_PROGRESS ───────→ REVIEWING        (有 manager 时，assignee 提交完成)
IN_PROGRESS ───────→ DONE             (无 manager 时，assignee 直接完成)
IN_PROGRESS ───────→ ON_HOLD
IN_PROGRESS ───────→ CANCELLED

REVIEWING ─────────→ DONE             (manager 验收通过)
REVIEWING ─────────→ IN_PROGRESS      (manager 打回，需返工)

ON_HOLD ───────────→ IN_PROGRESS
ON_HOLD ───────────→ CANCELLED
```

### 操作权限约束

| 状态变更 | 允许操作人 |
|----------|-----------|
| → REVIEWING | assignee |
| REVIEWING → DONE | manager |
| REVIEWING → IN_PROGRESS（打回）| manager |
| → CANCELLED | manager 或 creator |
| 其他状态变更 | assignee |

> **创建与分配权限：** 所有 Agent 均可创建任务，但 `assignee_id` 只能指定为创建人自己或其直接/间接下属 Agent（按组织架构层级判断）；违规时系统拒绝并返回错误。

> **依赖约束：** 尝试将有未完成依赖的任务置为 `IN_PROGRESS` 时，系统将拒绝并返回错误，提示哪些依赖任务尚未完成（任务应处于 `PENDING` 状态等待依赖满足）。

---

## 四、Agent 工具设计

### 4.1 create_task

创建一个新任务。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | str | ✅ | 任务标题 |
| `description` | str | | 任务详细描述，含上下文、约束和交付标准 |
| `assignee_id` | int | ✅ | 执行人 Agent ID |
| `manager_id` | int | | 管理/验收人 Agent ID，不填则无需验收 |
| `priority` | str | | 优先级：HIGH / NORMAL / LOW，默认 NORMAL |
| `parent_id` | int | | 父任务 ID，用于子任务拆解 |
| `depends_on` | list[int] | | 依赖的 task_id 列表 |
| `room_id` | int | | 关联房间 ID |

**返回：** `{ success, task_id, message }`

---

### 4.2 update_task

更新任务状态或结果。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | int | ✅ | 任务 ID |
| `status` | str | ✅ | 新状态 |
| `result` | str | | 完成/提交摘要（status=REVIEWING 或 DONE 时填写） |
| `block_reason` | str | | 搁置原因（status=ON_HOLD 时填写） |


**返回：** `{ success, task, message }`

---

### 4.3 get_task

查询单个任务详情。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | int | ✅ | 任务 ID |

**返回：** `{ success, task }` — 包含依赖任务的状态摘要

---

### 4.4 list_tasks

查询任务列表。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `assignee_id` | int | | 按执行人 Agent ID 过滤 |
| `manager_id` | int | | 按管理人 Agent ID 过滤（用于查看需要自己验收的任务） |
| `status` | str | | 按状态过滤 |
| `limit` | int | | 最多返回条数，默认 20 |

**返回：** `{ success, tasks, total }`

> **可见性：** 不做权限隔离，团队内所有 Agent 均可查看全部任务（按 team_id 隔离，但团队内完全透明）。

---

## 五、典型工作流

### 场景一：开发→测试流水线（含依赖）

```
1. PM 创建开发任务
   create_task(title="开发登录功能", assignee="阿张", manager="小马哥", priority="HIGH")
   → 返回 task_id=1

2. PM 创建测试任务，依赖开发任务
   create_task(title="测试登录功能", assignee="小测", manager="小马哥", depends_on=[1])
   → 任务自动进入 WAITING 状态（task_id=2）

3. 阿张开始开发
   update_task(task_id=1, status="IN_PROGRESS")

4. 阿张完成，提交验收
   update_task(task_id=1, status="REVIEWING", result="登录功能已完成，PR #12 已合并")

5. 小马哥验收通过
   update_task(task_id=1, status="DONE")

6. 小测的任务依赖已满足，开始测试
   update_task(task_id=2, status="IN_PROGRESS")

7. 小测完成测试，提交验收
   update_task(task_id=2, status="REVIEWING", result="测试通过，无阻塞问题")

8. 小马哥验收通过
   update_task(task_id=2, status="DONE")
```

### 场景二：验收打回，需返工

```
4. 阿张提交验收
   update_task(task_id=1, status="REVIEWING", result="登录功能完成")

5. 小马哥验收发现问题，打回（在房间里说明原因）
   update_task(task_id=1, status="IN_PROGRESS")

6. 阿张修改后再次提交
   update_task(task_id=1, status="REVIEWING", result="已修复加密逻辑")

7. 小马哥验收通过
   update_task(task_id=1, status="DONE")
```

---

## 六、待讨论事项

- [x] **任务权限**：所有 Agent 均可创建任务，但只能将任务分配给自己或自己的下属 Agent（按组织层级判断）。
- [x] **跨团队隔离**：无跨团队场景，tasks 按 team_id 严格隔离即可。
- [ ] **任务通知**：创建/分配任务后是否自动发消息通知被分配人？
- [x] **状态流转严格性**：权限约束严格执行，非授权人操作将被系统拒绝（如非 manager 不能执行验收通过/打回）。
- [x] **任务可见性**：`list_tasks` 不做隔离，团队内所有 Agent 均可查看全部任务，保持透明。

---

## 七、实现计划（待定）

1. DB migration：新增 tasks 表
2. Model 层：`GtTask` 数据模型
3. DAL 层：`gtTaskManager`（CRUD）
4. 工具层：4 个工具函数（create_task / update_task / get_task / list_tasks）
5. 注册到 `toolRegistry`（权限分类 TBD）
6. 测试

---

## 八、变更记录

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.1 | 2026-05-22 | 初始草稿，基础任务结构 + 依赖关系设计 |
| v0.2 | 2026-05-22 | 新增 manager 字段和 REVIEWING 状态，支持验收流程 |
| v0.3 | 2026-05-22 | assignee、creator、manager 改为存储 Agent ID（INTEGER） |
| v0.4 | 2026-05-22 | 移除 review_comment 字段，验收意见改由房间沟通 |
| v0.5 | 2026-05-22 | 状态重命名：WAITING → PENDING，BLOCKED → ON_HOLD，语义更直观 |
| v0.6 | 2026-05-22 | 确定任务权限：所有 Agent 可创建，assignee 限自己或下属；无跨团队场景 |
| v0.7 | 2026-05-22 | 确定任务可见性：list_tasks 不做隔离，团队内全员可见所有任务 |
| v0.8 | 2026-05-22 | 确定状态流转权限严格执行，非授权人操作一律拒绝 |
