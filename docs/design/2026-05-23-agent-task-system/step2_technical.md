# V22: Agent Task 系统 - 技术文档

## 1. 目标

为 Agent 协作引入结构化任务管理能力：新增 `agent_tasks` 数据表、`GtAgentTask` 数据模型、DAL 层 CRUD、以及 4 个注册到 funcToolService 的 Agent 工具（`create_task` / `update_task` / `get_task` / `list_tasks`）。

---

## 2. 命名说明

项目中已存在 `GtScheculeTask`（表 `schecule_tasks`），用于调度系统的内部任务队列，与本 feature 无关。本版本新增的协作任务模型命名为 **`GtAgentTask`**（表 `agent_tasks`），两者职责不同，名称区分。

---

## 3. 数据模型

### 3.1 GtAgentTask（新增）

文件：`src/model/dbModel/gtAgentTask.py`

```python
class GtAgentTask(DbModelBase):
    team_id:      int             = peewee.IntegerField()
    title:        str             = peewee.TextField()
    description:  str             = peewee.TextField(default='')
    assignee_id:  int             = peewee.IntegerField()
    creator_id:   int             = peewee.IntegerField()
    manager_id:   int | None      = peewee.IntegerField(null=True)
    status:       TaskStatus      = EnumField(TaskStatus, default=TaskStatus.TODO)
    priority:     TaskPriority    = EnumField(TaskPriority, default=TaskPriority.NORMAL)
    parent_id:    int | None      = peewee.IntegerField(null=True)
    depends_on:   list[int]       = JsonField(default=list)   # JSON 数组：依赖 task_id 列表
    room_id:      int | None      = peewee.IntegerField(null=True)
    result:       str             = peewee.TextField(default='')
    block_reason: str             = peewee.TextField(default='')

    class Meta:
        table_name = "agent_tasks"
        indexes = (
            (("team_id", "status"), False),
            (("team_id", "assignee_id"), False),
        )
```

`DbModelBase` 继承 `AutoTimestampMixin`，自动维护 `created_at` / `updated_at`。

### 3.2 新增枚举（constants.py）

```python
class TaskStatus(EnhanceEnum):
    TODO        = "TODO"
    PENDING     = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD     = "ON_HOLD"
    REVIEWING   = "REVIEWING"
    DONE        = "DONE"
    CANCELLED   = "CANCELLED"

class TaskPriority(EnhanceEnum):
    HIGH   = "HIGH"
    NORMAL = "NORMAL"
    LOW    = "LOW"
```

---

## 4. 状态机与权限约束

### 4.1 合法状态流转

```
TODO          → IN_PROGRESS / PENDING / CANCELLED
PENDING       → IN_PROGRESS / CANCELLED
IN_PROGRESS   → REVIEWING（有 manager）/ DONE（无 manager）/ ON_HOLD / CANCELLED
REVIEWING     → DONE / IN_PROGRESS
ON_HOLD       → IN_PROGRESS / CANCELLED
```

### 4.2 操作权限

| 变更 | 允许操作人 |
|------|-----------|
| → REVIEWING | assignee |
| REVIEWING → DONE | manager |
| REVIEWING → IN_PROGRESS（打回）| manager |
| → CANCELLED | manager 或 creator |
| 其余状态变更 | assignee |

### 4.3 依赖约束

`PENDING` 状态由创建时自动判定：若 `depends_on` 中有任何任务状态不为 `DONE`，则初始状态为 `PENDING`，否则为 `TODO`。

将 `PENDING` 任务置为 `IN_PROGRESS` 时，系统重新检查依赖；若仍有未完成依赖，拒绝操作并返回错误。

### 4.4 分配权限

`assignee_id` 只能指定为创建人自己或其直接/间接下属（按 `GtDept` 组织层级递归判断）；不满足时返回 `assignee_not_allowed` 错误。

---

## 5. DAL 层

文件：`src/dal/db/gtAgentTaskManager.py`

```python
async def create_task(task: GtAgentTask) -> GtAgentTask
async def get_task(task_id: int) -> GtAgentTask | None
async def list_tasks(
    team_id: int,
    assignee_id: int | None = None,
    manager_id:  int | None = None,
    status:      TaskStatus | None = None,
    limit: int = 20,
) -> list[GtAgentTask]
async def update_task(task: GtAgentTask, fields: list) -> GtAgentTask
async def get_tasks_by_ids(task_ids: list[int]) -> list[GtAgentTask]
```

`update_task` 使用 `task.aio_save(only=fields)` 精确更新指定字段，同时确保 `updated_at` 被刷新。

---

## 6. 工具层

### 6.1 工具函数（tools.py 或独立 taskTools.py）

4 个工具函数均接受 `_context: ToolCallContext | None` 作为最后一个隐式参数（由 funcToolService 自动注入，不暴露给 LLM）。

#### create_task

```python
async def create_task(
    title: str,
    assignee_id: int,
    description: str = '',
    manager_id: Optional[int] = None,
    priority: str = 'NORMAL',
    parent_id: Optional[int] = None,
    depends_on: Optional[list] = None,
    room_id: Optional[int] = None,
    _context: Optional[ToolCallContext] = None,
) -> dict
```

返回：`{ success, task_id, message }`

逻辑：
1. 从 `_context` 取 `team_id` 和 `caller_agent_id`（作为 `creator_id`）
2. 验证 `assignee_id` 为创建人自身或下属（查 GtDept 递归）
3. 验证 `depends_on` 中所有 task_id 属于同一 team
4. 检查依赖是否满足，决定初始状态（`TODO` 或 `PENDING`）
5. 写库，返回 `task_id`

#### update_task

```python
async def update_task(
    task_id: int,
    status: str,
    result: str = '',
    block_reason: str = '',
    _context: Optional[ToolCallContext] = None,
) -> dict
```

返回：`{ success, task, message }`（`task` 为 GtAgentTask 序列化结果）

逻辑：
1. 加载任务，验证 `team_id` 匹配
2. 根据目标状态和当前调用者身份，校验操作权限（见 §4.2）
3. 若目标状态为 `IN_PROGRESS` 且当前为 `PENDING`，重新检查依赖
4. 执行合法性验证（状态流转是否允许）
5. 更新 `status`、`result`（如提供）、`block_reason`（如提供）、`updated_at`

#### get_task

```python
async def get_task(
    task_id: int,
    _context: Optional[ToolCallContext] = None,
) -> dict
```

返回：`{ success, task }`，`task` 中包含 `depends_on_details`（依赖任务的 id/title/status 摘要列表）

#### list_tasks

```python
async def list_tasks(
    assignee_id: Optional[int] = None,
    manager_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
    _context: Optional[ToolCallContext] = None,
) -> dict
```

返回：`{ success, tasks, total }`

### 6.2 工具注册（core.py）

在 `load_func_tools()` 中追加：

```python
"create_task":  create_task,
"update_task":  update_task,
"get_task":     get_task,
"list_tasks":   list_tasks,
```

工具归属 `ToolCategory`（TBD，按现有分类规则确定）。

---

## 7. DB Migration

新增 `agent_tasks` 表的迁移脚本，与项目现有迁移机制保持一致。

`GtAgentTask` 需在数据库初始化时注册到 `model_list`（与 `GtScheculeTask` 并列，不冲突）。

---

## 8. 改动范围

| 文件 | 变更 |
|------|------|
| `src/constants.py` | 新增 `TaskStatus`、`TaskPriority` 枚举 |
| `src/model/dbModel/gtAgentTask.py`（新文件） | `GtAgentTask` 数据模型 |
| `src/model/dbModel/__init__.py` | 导出 `GtAgentTask` |
| `src/dal/db/gtAgentTaskManager.py`（新文件） | CRUD 方法 |
| `src/dal/db/__init__.py` | 导出 `gtAgentTaskManager` |
| `src/service/funcToolService/tools.py`（或新 taskTools.py） | 4 个工具函数 |
| `src/service/funcToolService/core.py` | 注册 4 个工具 |
| `src/service/funcToolService/__init__.py`（如需） | 导出新工具 |
| DB migration 脚本 | 建 `agent_tasks` 表 |

---

## 9. 测试要点

### 单元测试

- `create_task`：assignee 为下属时成功，assignee 为非下属时返回 `assignee_not_allowed`
- `create_task`：depends_on 有未完成任务时初始状态为 PENDING，全部 DONE 时为 TODO
- `update_task`：非授权人操作受限状态（如非 manager 执行验收通过）时返回权限错误
- `update_task`：PENDING 状态在依赖未满足时拒绝置为 IN_PROGRESS
- `update_task`：无 manager 时 assignee 可直接 IN_PROGRESS → DONE
- `update_task`：有 manager 时 assignee 只能 IN_PROGRESS → REVIEWING
- `get_task`：返回值包含 `depends_on_details`，摘要信息准确
- `list_tasks`：按 assignee_id / manager_id / status 过滤结果正确

### 集成测试

- 完整流水线：create → IN_PROGRESS → REVIEWING → DONE（有 manager）
- 打回流程：REVIEWING → IN_PROGRESS → REVIEWING → DONE
- 依赖流水线：task_2 depends_on task_1，task_1 DONE 后 task_2 可推进
