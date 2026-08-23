# V11：开发任务计划

## 依赖关系总览

```
1. 新增常量（枚举）
   └─ 2. ORM 模型 GtAgentActivity
      └─ 3. DAL Manager
         └─ 4. agentActivityService
            ├─ 5. wsController 扩展
            ├─ 6. REST 查询接口
            ├─ 7. agentTaskConsumer activate/idle 打点
            └─ 8. agentTurnRunner tool/compact/非流式 infer 打点
               └─ 11. agentTurnRunner 流式 infer 打点（实时 token）
                     └─ 12. 测试

9. llmApiUtil 流式回调
   └─ 10. llmService infer_stream
         └─ 11（同上）
```

可并行执行的点：
- 任务 1 开始后，任务 9（llmApiUtil）可独立同步推进
- 任务 4 完成后，任务 5/6/7/8 可并行开发

---

## 任务列表

### 任务 1：新增常量枚举

**文件**：`src/constants.py`

新增：
- `AgentActivityType(EnhanceEnum)`：`LLM_INFER, TOOL_CALL, COMPACT, ACTIVATE, IDLE`
- `AgentActivityStatus(EnhanceEnum)`：`STARTED, SUCCEEDED, FAILED, CANCELLED`
- `MessageBusTopic.AGENT_ACTIVITY_CHANGED`

延续项目 `EnhanceEnum + auto()` 约定。

---

### 任务 2：新增 ORM 模型 GtAgentActivity

**文件**：`src/model/dbModel/gtAgentActivity.py`（新建）

```python
class GtAgentActivity(DbModelBase):
    agent_id: int = peewee.IntegerField(index=True)
    team_id: int = peewee.IntegerField(index=True)
    activity_type: AgentActivityType = EnumField(AgentActivityType, null=False)
    status: AgentActivityStatus = EnumField(AgentActivityStatus, null=False)
    title: str = peewee.CharField()
    detail: str = peewee.TextField(default="")
    error_message: str | None = peewee.TextField(null=True)
    started_at: datetime = peewee.DateTimeField()
    finished_at: datetime | None = peewee.DateTimeField(null=True)
    duration_ms: int | None = peewee.IntegerField(null=True)
    metadata: dict = JsonField(default=dict)

    class Meta:
        table_name = "agent_activities"
        indexes = (
            (("team_id", "id"), False),
            (("agent_id", "id"), False),
        )
```

同时在 `ormService` 中注册新表以触发建表。

**前置**：任务 1

---

### 任务 3：新增 DAL Manager

**文件**：`src/dal/db/gtAgentActivityManager.py`（新建）

接口：
```python
async def create_activity(item: GtAgentActivity) -> GtAgentActivity
async def update_activity_by_id(activity_id: int, **fields) -> GtAgentActivity
async def list_agent_activities(agent_id: int, limit: int = 100) -> list[GtAgentActivity]
async def list_team_activities(team_id: int, limit: int = 200) -> list[GtAgentActivity]
async def list_activities(
    room_id: int | None = None,
    team_id: int | None = None,
    agent_id: int | None = None,
    limit: int = 200,
) -> list[GtAgentActivity]
```

约束：
- update 允许更新：`status, detail, error_message, finished_at, duration_ms, metadata`
- 所有查询按 `id desc` 排序
- `room_id` 过滤通过 `json_extract(metadata, '$.room_id')` 或应用层过滤实现

**前置**：任务 2

---

### 任务 4：新增 agentActivityService

**文件**：`src/service/agentActivityService.py`（新建）

接口：
```python
async def add_activity(
    *, agent_id: int, team_id: int, activity_type: AgentActivityType,
    status: AgentActivityStatus = AgentActivityStatus.STARTED,
    title: str, detail: str = "", error_message: str | None = None,
    metadata: dict | None = None,
) -> GtAgentActivity

async def update_activity_progress(
    activity_id: int, *,
    status: AgentActivityStatus | None = None,
    detail: str | None = None,
    error_message: str | None = None,
    metadata_patch: dict | None = None,
) -> GtAgentActivity
```

约定：
- 若创建/更新时传入结束态（SUCCEEDED/FAILED/CANCELLED），自动补 `finished_at` 与 `duration_ms`
- `metadata_patch` 采用浅合并（读取 → merge → 写回）
- 每次 add/update 后向 `messageBus` 广播 `AGENT_ACTIVITY_CHANGED`
- 广播 payload 格式：`{ "event": "agent_activity", "data": { ...activity 字段... } }`

**前置**：任务 3

---

### 任务 5：扩展 wsController

**文件**：`src/controller/wsController.py`

在 `EventsWsHandler` 中：
- 新增订阅 `MessageBusTopic.AGENT_ACTIVITY_CHANGED`
- 映射为 `event = "agent_activity"` 向客户端推送

**前置**：任务 4

---

### 任务 6：新增活动记录查询 REST 接口

**文件**：`src/controller/activityController.py`（新建），`src/route.py`（注册路由）

接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agents/{agent_id}/activities.json` | 查询某个 Agent 的活动记录 |
| GET | `/teams/{team_id}/activities.json` | 查询某个 Team 的活动记录 |
| GET | `/activities.json?room_id={room_id}` | 按 room_id 过滤活动记录 |

返回格式：`{ "activities": [...] }`，按 `id desc` 排序。

**前置**：任务 4

---

### 任务 7：agentTaskConsumer 接入 activate/idle 活动记录

**文件**：`src/service/agentService/agentTaskConsumer.py`

- 切换到 `ACTIVE` 时：广播 `AGENT_STATUS_CHANGED` 后，调用 `add_activity(activity_type=ACTIVATE, status=SUCCEEDED, ...)`
- 切换到 `IDLE` 时：广播 `AGENT_STATUS_CHANGED` 后，调用 `add_activity(activity_type=IDLE, status=SUCCEEDED, ...)`
- 活动记录写入与状态切换在同一处，避免时间线脱节

**前置**：任务 4

---

### 任务 8：agentTurnRunner 接入 tool_call / compact / 非流式 llm_infer 活动记录

**文件**：`src/service/agentService/agentTurnRunner.py`

**tool_call**（`_run_tool_to_item`）：
1. `add_activity(TOOL_CALL, STARTED, title="调用工具", metadata={"tool_name": ...})`
2. 执行工具
3. `update_activity_progress(SUCCEEDED / FAILED, ...)`

**compact**（`_execute_compact`）：
1. `add_activity(COMPACT, STARTED, title="压缩上下文", metadata={"compact_stage": ...})`
2. 执行 compact
3. `update_activity_progress(SUCCEEDED / FAILED, ...)`

**llm_infer 非流式**（`_infer_to_item`，暂用 `infer`）：
1. `add_activity(LLM_INFER, STARTED, title="推理", ...)`
2. 执行推理
3. `update_activity_progress(SUCCEEDED / FAILED, ...)`

**overflow retry 活动表达**：
- 首次 llm_infer 因 context overflow 失败时：`metadata.error_kind = "context_overflow"`，status=FAILED
- 随后落一条 compact 记录
- compact 成功后再建新的 llm_infer 记录

**前置**：任务 4

---

### 任务 9：扩展 llmApiUtil 支持流式 chunk 回调

**文件**：`src/util/llmApiUtil/client.py`

扩展 `send_request_stream(...)`，新增参数：
```python
on_chunk: Callable[[ModelResponseStream], Awaitable[None] | None] | None = None
```

处理流程：
1. 迭代 `CustomStreamWrapper`
2. 每收到 chunk：收集到 `chunks`，若有 `on_chunk` 立即回调
3. 全部 chunk 结束后调用 `stream_chunk_builder(...)` 聚合，返回原有 `OpenAIResponse`

**前置**：无（可与任务 1 并行）

---

### 任务 10：扩展 llmService 新增 infer_stream 接口

**文件**：`src/service/llmService.py`

新增：
```python
@dataclass
class InferStreamProgress:
    delta_text: str
    current_completion_tokens: int | None = None
    current_total_tokens: int | None = None

async def infer_stream(
    model: str | None,
    ctx: GtCoreAgentDialogContext,
    on_progress: Callable[[InferStreamProgress], Awaitable[None] | None] | None = None,
) -> InferResult:
```

token 统计优先级：
1. 优先使用 chunk 自带 usage
2. 若上游不提供，本地估算 completion tokens
3. 推理结束后以最终响应的 `usage` 覆盖 `final_*` 字段

最终返回与 `infer()` 一致的 `InferResult`。

**前置**：任务 9

---

### 任务 11：agentTurnRunner 接入 llm_infer 流式活动记录

**文件**：`src/service/agentService/agentTurnRunner.py`

将任务 8 中的非流式 llm_infer 打点升级为流式：
- 调用 `llmService.infer_stream(...)`
- `on_progress` 回调中调用 `update_activity_progress(metadata_patch={"current_completion_tokens": ..., "current_total_tokens": ...})`
- 引入节流：每累计 N 个 chunk 或每 200ms 刷新一次，避免高频 IO
- 推理结束后以 `final_*` token 字段更新记录并标记 SUCCEEDED/FAILED

**前置**：任务 8、任务 10

---

### 任务 12：补充单元测试、集成测试和 WebSocket 测试

覆盖场景：
- `agentActivityService.add_activity` / `update_activity_progress`（结束态自动补字段、metadata 浅合并）
- DAL Manager CRUD
- agentTaskConsumer 的 activate/idle 活动记录写入
- agentTurnRunner 的 tool_call / compact / llm_infer 活动记录生命周期
- overflow retry 活动时间线（failed infer → compact → new infer）
- REST 查询接口（by agent_id / team_id / room_id）
- WebSocket 广播 agent_activity 事件

**前置**：任务 5、6、7、11
