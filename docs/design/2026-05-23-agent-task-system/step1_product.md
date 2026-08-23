# V22: Agent Task 系统 - 产品文档

## 目标

为 Agent 团队提供一套轻量级的结构化任务管理能力，使 Agent 间的任务协作从"消息流中约定"升级为"可追踪、可验收的正式任务"。

当前 Agent 团队的任务分工完全依赖聊天消息，导致任务状态隐藏在消息流中难以全局追踪、分配与进展无法持久化查询、任务间依赖关系无法表达。V22 通过引入 Task 工具，让 Agent 能够以结构化方式创建、分配、跟进和验收任务，同时保持与现有聊天协作流程的自然融合。

---

## 功能特性

### 一、结构化任务创建与分配

Agent 可通过 `create_task` 工具显式创建一个任务，明确指定：

- **标题与描述**：任务目标、上下文约束和交付标准
- **执行人（assignee）**：负责完成任务的 Agent
- **管理人（manager）**：负责验收的 Agent（可选，不设则无需验收）
- **优先级**：HIGH / NORMAL / LOW
- **父任务**：支持子任务拆解
- **依赖任务**：前置依赖完成后才允许开始

**创建权限**：所有 Agent 均可创建任务，但 assignee 只能指定为创建人自己或其直接/间接下属（按组织层级）。

### 二、任务状态机与全生命周期跟踪

每个任务有清晰的状态流转路径，反映真实工作进展：

| 状态 | 说明 |
|------|------|
| `TODO` | 待处理，初始状态 |
| `PENDING` | 等待前置依赖完成 |
| `IN_PROGRESS` | 执行中 |
| `ON_HOLD` | 执行中途搁置（需外部信息或决策） |
| `REVIEWING` | 待验收（assignee 提交，等待 manager 审核） |
| `DONE` | 已完成 |
| `CANCELLED` | 已取消 |

状态变更操作受权限约束：assignee 推进任务进展，manager 负责验收通过或打回，creator 和 manager 均可取消任务。

### 三、任务依赖管理

创建任务时可声明依赖的其他任务 ID。当依赖存在未完成任务时：

- 新创建的任务自动进入 `PENDING` 状态
- 尝试将 `PENDING` 任务置为 `IN_PROGRESS` 时，系统拒绝操作并提示哪些依赖尚未完成
- 所有依赖满足后，assignee 可正常推进

这一机制让 Agent 可以构建开发→测试、设计→实现等有序流水线，无需在消息里人工协调前后顺序。

### 四、验收流程（REVIEWING）

当任务设置了 manager 时，启用验收流程：

1. assignee 完成工作后，将任务状态更新为 `REVIEWING`，并填写交付摘要
2. manager 查看交付内容，决定：
   - **验收通过** → 任务流转至 `DONE`
   - **打回返工** → 任务流转回 `IN_PROGRESS`，manager 在房间中说明问题

未设置 manager 的任务，assignee 可直接将状态更新为 `DONE`，无需验收环节。

### 五、搁置原因记录（ON_HOLD）

当任务因外部原因无法继续推进时，assignee 可将其置为 `ON_HOLD` 并记录搁置原因（如"等待接口文档"、"等待产品决策"），让全团队清楚阻塞点，避免催促无用功。

### 六、任务查询与全团队透明

Agent 可通过 `list_tasks` 和 `get_task` 工具查询任务：

- `list_tasks`：按执行人、管理人、状态等条件过滤，查看任务列表
- `get_task`：查询单个任务详情，包含依赖任务的状态摘要

**可见性原则**：任务不做权限隔离，团队内所有 Agent 均可查看全部任务，保持协作透明度。任务按 team_id 严格隔离，不同团队不互见。

---

## 用户价值

### 1. 任务不再"消失在消息流里"

以前 PM 分配一个任务，靠一条消息完成。有没有看见、有没有开始、卡在哪了，都要靠猜或者继续问。现在任务显式存在，状态一目了然，执行人和管理人明确到位。

### 2. 依赖自动协调，不用手动盯

设计→开发→测试的流水线依赖，以前需要靠消息通知"我好了，你可以开始了"。现在声明依赖关系后，系统自动管理前后顺序，前置任务完成，后续任务自动解锁。

### 3. 验收流程有据可查

以前验收结果只在消息里体现，找起来靠翻聊天记录。现在 assignee 提交摘要、manager 通过或打回，都记录在任务中，全过程结构化保存。

### 4. 阻塞点透明可见

以前一个任务卡住了，外部很难察觉，也不知道是谁的问题。ON_HOLD 状态加搁置原因，让全团队一眼看出"谁卡住了、为什么卡住"。

---

## 工具说明

V22 为 Agent 提供 4 个新工具：

### create_task

创建新任务，返回任务 ID。

**关键参数：** `title`（必填）、`assignee_id`（必填）、`description`、`manager_id`、`priority`、`parent_id`、`depends_on`、`room_id`

### update_task

更新任务状态或附加信息。

**关键参数：** `task_id`（必填）、`status`（必填）、`result`（提交摘要）、`block_reason`（搁置原因）

### get_task

查询单个任务详情，包含依赖任务状态摘要。

**关键参数：** `task_id`（必填）

### list_tasks

查询任务列表，支持多维度过滤。

**关键参数：** `assignee_id`、`manager_id`、`status`、`limit`（默认 20）

---

## 典型场景

### 场景一：开发 → 测试流水线

```
1. PM 创建开发任务（assignee=阿张, manager=PM）→ task_id=1
2. PM 创建测试任务（assignee=小测, manager=PM, depends_on=[1]）→ task_id=2，自动进入 PENDING

3. 阿张开始开发：update_task(task_id=1, status="IN_PROGRESS")
4. 阿张完成，提交验收：update_task(task_id=1, status="REVIEWING", result="PR #12 已合并")
5. PM 验收通过：update_task(task_id=1, status="DONE")

6. 小测的依赖已满足，开始测试：update_task(task_id=2, status="IN_PROGRESS")
7. 小测完成，提交验收：update_task(task_id=2, status="REVIEWING", result="测试通过")
8. PM 验收通过：update_task(task_id=2, status="DONE")
```

### 场景二：验收打回，需返工

```
1. 阿张提交验收：update_task(task_id=1, status="REVIEWING", result="登录功能完成")
2. PM 发现加密逻辑有问题，在房间里说明后打回：update_task(task_id=1, status="IN_PROGRESS")
3. 阿张修复后再次提交：update_task(task_id=1, status="REVIEWING", result="已修复加密逻辑")
4. PM 验收通过：update_task(task_id=1, status="DONE")
```

### 场景三：任务中途被外部阻塞

```
1. 阿张发现接口文档还没定稿，无法继续
   update_task(task_id=3, status="ON_HOLD", block_reason="等待接口文档定稿，预计明天")
2. 接口文档完成后，阿张继续
   update_task(task_id=3, status="IN_PROGRESS")
```

---

## 产品边界

### V22 包含

- `agent_tasks` 数据表（含状态机、依赖关系、验收字段）
- 4 个 Agent 工具：`create_task`、`update_task`、`get_task`、`list_tasks`
- 任务状态流转的权限约束（assignee/manager/creator 各自的操作范围）
- 依赖任务的自动状态管理（PENDING 自动进入，依赖满足后解锁）
- 团队内任务全透明（不做可见性隔离）

### V22 不包含

- 前端任务可视化界面（无任务看板、任务列表 UI）
- 任务创建/分配的自动通知（通过房间消息通知被分配人，由 Agent 自行决定是否通知）
- 跨团队任务协作
- 任务的批量操作
- 任务模板或任务类型分类

---

## 验收标准

### 任务创建与权限

- Agent 可通过 `create_task` 创建任务，返回任务 ID
- assignee 只能指定为创建人自己或其下属，违规时返回错误
- 有依赖任务且依赖未完成时，新任务自动进入 `PENDING` 状态

### 状态流转

- `TODO` / `PENDING` → `IN_PROGRESS`：assignee 操作，有未完成依赖时系统拒绝
- `IN_PROGRESS` → `REVIEWING`：assignee 操作（有 manager 时）
- `IN_PROGRESS` → `DONE`：assignee 操作（无 manager 时）
- `REVIEWING` → `DONE` / `IN_PROGRESS`：manager 操作
- → `CANCELLED`：manager 或 creator 操作
- 非授权人执行受限操作时，系统拒绝并返回错误

### 任务查询

- `get_task` 返回任务详情及依赖任务的状态摘要
- `list_tasks` 支持按 assignee_id、manager_id、status 过滤，返回指定数量任务
- 团队内所有 Agent 均可查看全部任务（team_id 隔离）

### 数据一致性

- 任务状态变更时 `updated_at` 同步更新
- `depends_on` 为合法 JSON 数组，任务 ID 均为同团队内有效任务
