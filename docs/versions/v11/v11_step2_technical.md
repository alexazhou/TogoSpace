# V11: Agent 活动记录与运行态可观测性 - 技术文档

## 1. 架构概览

V11 的目标不是改写现有 Agent 调度逻辑，而是在现有运行链路上补齐一条“活动记录与运行态广播”旁路。当前系统已经具备三类基础能力：

- `AgentTaskConsumer + AgentTurnRunner`：前者负责 Agent 级任务消费、状态流转与外层执行控制，后者负责单个 turn 内的推理、工具调用、compact 等核心执行路径
- `messageBus + wsController`：承载后端到前端的实时广播
- `ORM + DAL + persistenceService`：承载本地 SQLite 持久化

因此 V11 采用“**在现有执行路径上打点**”而不是“重做一套执行框架”的方案：

- **动作侧**：为 `llm_infer`、`tool_call`、`compact` 创建独立活动记录
- **状态侧**：继续保留 Agent 的 `ACTIVE / IDLE / FAILED` 运行态作为“当前状态”真源，同时在 `ACTIVE ↔ IDLE` 切换时写入 `activate / idle` 活动记录
- **广播侧**：在现有 `messageBus` 上新增活动主题，通过 `wsController` 广播给前端
- **存储侧**：新增 `agent_activities` 表，独立于 `agent_histories`

这里需要特别区分两层执行角色：

- `AgentTaskConsumer`：位于 Agent 的任务消费层，负责取任务、驱动一次消费过程、切换 `ACTIVE / IDLE / FAILED` 运行态，并在内部持有 `AgentTurnRunner`
- `AgentTurnRunner`：位于单次 turn 的执行层，负责 host loop、消息同步、LLM 推理、tool call、compact 等细粒度动作编排
- 因此 V11 的观测打点会分布在两层：运行态切换主要接在 `AgentTaskConsumer`，动作活动记录主要接在 `AgentTurnRunner`

运行路径示意：

```text
AgentTaskConsumer
        │
        ├─ 取任务 / 驱动消费 / 切换 ACTIVE|IDLE|FAILED
        │        │
        │        ├─ messageBus.publish(MessageBusTopic.AGENT_STATUS_CHANGED, ...)
        │        ├─ agentActivityService.add_activity(status=SUCCEEDED, ... activate / idle ...)
        │        └─ 调用 AgentTurnRunner.run_turn(...)
        │
        └─ AgentTurnRunner
                 │
                 ├─ host loop / 消息同步 / 推理 / 工具 / compact
                 │        │
                 │        └─ agentActivityService.add/update(...)
                 │
                 ├─ DAL 持久化 agent_activities
                 │
                 └─ messageBus.publish(MessageBusTopic.AGENT_ACTIVITY_CHANGED, ...)
                          │
                          └─ wsController -> WebSocket 前端
```

V11 不改变 Room、Task、History 的主数据语义；活动记录是附加的观测面数据。

---

## 2. 核心设计原则

### 2.1 活动记录与消息历史严格解耦

- `agent_histories` 继续只保存对话上下文、tool result 和 compact summary
- `agent_activities` 专门保存运行中的动作记录
- 活动记录不进入 Prompt，不参与 `build_infer_messages()`

### 2.2 直接使用递增 `id` 表达顺序，不保存“是否最后一条”字段

活动记录直接使用数据库自增 `id` 作为顺序依据：

- 后创建的记录拥有更大的 `id`
- 某个 Agent 的最新一条记录，就是该 Agent 当前 `id` 最大的记录
- 不新增 `is_latest` 之类会不断变化的持久化字段

这与产品文档中的展示规则一致：前端以 `id` 排序即可得到活动时间线。

### 2.3 动作类型与控制流解耦

以下控制流概念**不单独建模为活动类型**：

- `overflow_retry`
- `scheduler_wait`

其表达方式分别为：

- `overflow_retry`：拆成失败的 `llm_infer`、后续 `compact`、以及新一次 `llm_infer`
- `scheduler_wait`：由现有 `AgentStatus.ACTIVE / IDLE` 当前状态表达，并在切换时落 `activate / idle` 活动记录

### 2.4 流式更新复用同一条活动记录

`llm_infer` 在流式返回期间不会不断新增记录，而是：

1. 创建 1 条 `started` 记录
2. 在流式 chunk 到达时不断更新这条记录的 `metadata.current_*`
3. 结束时将同一条记录更新为 `succeeded` / `failed`

因此：

- 一个推理动作对应一条活动记录
- 多次 WebSocket 推送对应同一条记录的多次状态/进度更新

### 2.5 运行态切换采用“状态通道 + 活动记录”双轨表达

当前系统已有：

- `AgentStatus.ACTIVE / IDLE / FAILED`
- `MessageBusTopic.AGENT_STATUS_CHANGED`
- `wsController` 中的 `agent_status` WebSocket 事件

V11 的处理方式是：

- `AGENT_STATUS_CHANGED` 继续承载 Agent 当前状态的变化
- `agent_activities` 中新增 `activate / idle` 记录，用于保留状态切换时间线
- 两条链路并行存在，互不替代

---

## 3. 数据模型设计

### 3.1 新增活动类型枚举

位置建议：`src/constants.py`

```python
from enum import auto


class AgentActivityType(EnhanceEnum):
    LLM_INFER = auto()
    TOOL_CALL = auto()
    COMPACT = auto()
    ACTIVATE = auto()
    IDLE = auto()


class AgentActivityStatus(EnhanceEnum):
    STARTED = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    CANCELLED = auto()
```

说明：

- 内部枚举定义延续项目当前 `EnhanceEnum + auto()` 的约定
- 落库与对外 WebSocket / REST payload 沿用现有 `JSONUtil` 序列化链路处理，无需在这里额外约定枚举值字符串
- `ACTIVE / IDLE / FAILED` 仍属于 Agent 当前运行态，不放进 `AgentActivityStatus`
- `ACTIVATE / IDLE` 属于活动记录类型，用于表达一次状态切换事件
- `AgentActivityStatus` 只用于描述某一条活动记录本身的生命周期

### 3.2 新增 ORM 模型 `GtAgentActivity`

位置建议：`src/model/dbModel/gtAgentActivity.py`

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

字段说明：

- `title`：稳定动作名，如“推理”“调用工具”“压缩上下文”
- `detail`：动作说明，如工具名、compact 范围、推理阶段说明
- `metadata`：保存可选上下文，不把 `room_id` 拉成固定主列
- `activate / idle` 记录也复用同一张表；例如 `detail` 可写“收到调度后开始消费 turn”或“当前队列为空，进入等待”

### 3.3 `metadata` 约定

`metadata` 为松散扩展字段，但 Step 1 约定以下常用键：

```json
{
  "room_id": 12,
  "model": "gpt-5.4",
  "tool_name": "WebSearch",
  "tool_call_id": "call_xxx",
  "compact_stage": "pre",
  "estimated_prompt_tokens": 1820,
  "current_completion_tokens": 128,
  "current_total_tokens": 1948,
  "final_prompt_tokens": 1820,
  "final_completion_tokens": 512,
  "final_total_tokens": 2332
}
```

说明：

- `room_id` 是可选上下文，只放在 `metadata`
- token 相关字段只有 `llm_infer` 会使用
- `compact_stage` 对应当前已有 `HistoryUsage.compact_stage`

---

## 4. 数据库设计

### 4.1 新增 `agent_activities` 表

```sql
CREATE TABLE agent_activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL REFERENCES agents(id),
  team_id INTEGER NOT NULL REFERENCES teams(id),
  activity_type TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  error_message TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_agent_activities_team_id
ON agent_activities(team_id, id);

CREATE INDEX idx_agent_activities_agent_id
ON agent_activities(agent_id, id);
```

### 4.2 查询策略

- 某个 Agent 的活动列表：按 `agent_id + id desc` 查询
- 某个 Team 的活动列表：按 `team_id + id desc` 查询
- 某个 Room 的活动列表：通过 `metadata.room_id` 过滤

SQLite 中 `metadata.room_id` 的筛选可在 Step 1 先通过应用层过滤或 `json_extract(metadata, '$.room_id')` 实现；不强制在首版为其建立单独索引。

---

## 5. DAL 与 Service 分层

### 5.1 新增 DAL Manager

位置建议：`src/dal/db/gtAgentActivityManager.py`

主要接口：

```python
async def create_activity(item: GtAgentActivity) -> GtAgentActivity
async def update_activity_by_id(activity_id: int, **fields) -> GtAgentActivity
async def list_agent_activities(agent_id: int, limit: int = 100) -> list[GtAgentActivity]
async def list_team_activities(team_id: int, limit: int = 200) -> list[GtAgentActivity]
async def list_activities(room_id: int | None = None, team_id: int | None = None,
                          agent_id: int | None = None, limit: int = 200) -> list[GtAgentActivity]
```

约束：

- update 允许只更新状态、detail、error_message、finished_at、duration_ms、metadata

### 5.2 新增 `agentActivityService`

位置建议：`src/service/agentActivityService.py`

职责：

- 为上层提供稳定的 `add / update` 接口
- 统一计算 `duration_ms`
- 统一向 `messageBus` 广播 `AGENT_ACTIVITY_CHANGED`
- 统一将 ORM 记录转换为前端 payload
- `activate / idle` 这类状态切换活动也复用同一套接口，不再单独提供包装方法

建议接口：

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

`add_activity(...)` 的约定：

- 默认 `status=STARTED`，适用于 `llm_infer / tool_call / compact` 这类会持续推进的活动
- 也允许在创建时直接传 `status=SUCCEEDED / FAILED / CANCELLED`，适用于 `activate / idle` 这类瞬时完成的活动
- 若创建时传入结束态，服务内部统一补齐 `finished_at` 与 `duration_ms`
- 若创建时为失败态，可同时写入 `error_message`

`update_activity_progress(...)` 的约定：

- 不传 `status` 时，用于过程中的进度刷新
- 传 `status=SUCCEEDED / FAILED / CANCELLED` 时，用于收口活动生命周期
- 若传入结束态，服务内部统一补齐 `finished_at` 与 `duration_ms`
- 失败场景可同时携带 `error_message`

`metadata_patch` 采用 merge 策略：

- 读取原 metadata
- 执行浅合并
- 写回数据库

这样流式 token 更新时只需 patch `current_completion_tokens` / `current_total_tokens`。

---

## 6. 消息总线与 WebSocket 扩展

### 6.1 新增消息总线主题

位置：`src/constants.py`

```python
class MessageBusTopic(EnhanceEnum):
    ROOM_AGENT_TURN = auto()
    ROOM_MSG_ADDED = auto()
    AGENT_STATUS_CHANGED = auto()
    AGENT_ACTIVITY_CHANGED = auto()
```

### 6.2 扩展 `wsController`

当前 `EventsWsHandler` 只订阅：

- `ROOM_MSG_ADDED`
- `AGENT_STATUS_CHANGED`

V11 新增：

- 订阅 `AGENT_ACTIVITY_CHANGED`
- 将其映射为 `event = "agent_activity"`

WebSocket payload 统一格式：

```json
{
  "event": "agent_activity",
  "data": {
    "id": 1025,
    "agent_id": 5,
    "team_id": 1,
    "activity_type": "llm_infer",
    "status": "started",
    "title": "推理",
    "detail": "整理最终答复",
    "started_at": "2026-04-07T20:15:33+08:00",
    "finished_at": null,
    "duration_ms": null,
    "error_message": null,
    "metadata": {
      "room_id": 12,
      "current_completion_tokens": 128,
      "current_total_tokens": 1948
    }
  }
}
```

注意：

- `id` 既是记录唯一标识，也可直接作为时间线排序依据
- 同一活动记录的多次流式更新，`id` 保持不变，只更新 `status/detail/metadata/updated_at`

### 6.3 Agent 运行态切换

`active / idle / failed` 继续走现有：

- `AgentTaskConsumer.status`
- `MessageBusTopic.AGENT_STATUS_CHANGED`
- `ws event = "agent_status"`

同时补充活动记录侧：

- 切换到 `ACTIVE` 时，使用 `add_activity(activity_type=ACTIVATE, status=SUCCEEDED, ...)` 直接写入一条已完成活动记录
- 切换到 `IDLE` 时，使用 `add_activity(activity_type=IDLE, status=SUCCEEDED, ...)` 直接写入一条已完成活动记录
- `FAILED` 仍主要通过当前状态表达；具体失败原因由对应的 `llm_infer / tool_call / compact` 活动记录承载

建议接入点：

- `src/service/agentService/agentTaskConsumer.py`
- 在状态真正发生变化并广播 `AGENT_STATUS_CHANGED` 的同一处，补写对应活动记录，避免状态与时间线脱节

---

## 7. 推理链路改造

### 7.1 扩展 `llmService`

当前 `llmService.infer(...)` 只走非流式 `send_request_non_stream(...)`。

V11 需要新增流式能力，建议提供：

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
    ...
```

实现路径：

1. `llmService.infer_stream(...)` 调用 `llmApiUtil.send_request_stream(...)`
2. `llmApiUtil` 需要新增“边迭代 chunk、边回调 progress、最后再聚合完整响应”的能力
3. 完成后仍返回与非流式一致的 `InferResult`

### 7.2 `llmApiUtil` 的建议改造

当前 `send_request_stream(...)` 是“收集所有 chunk 后再聚合”，无法中途回调。

V11 建议改为：

```python
async def send_request_stream(
    request: OpenAIRequest,
    ...,
    on_chunk: Callable[[ModelResponseStream], Awaitable[None] | None] | None = None,
) -> OpenAIResponse:
```

处理流程：

1. 迭代 `CustomStreamWrapper`
2. 每收到一个 chunk：
   - 收集到 `chunks`
   - 若提供 `on_chunk`，则立即回调
3. 所有 chunk 结束后再调用 `stream_chunk_builder(...)`

### 7.3 token 统计策略

流式更新时优先级如下：

1. **优先使用上游 chunk 自带 usage**
2. 若上游不提供，则基于已收到文本做本地 completion token 估算
3. 推理结束后，以最终响应里的 `usage` 覆盖 `final_*` 字段

这样可以保证：

- 推理中有“当前 token 数”可展示
- 推理结束后有“最终 token 数”可结算

### 7.4 `AgentTurnRunner` 打点位置

#### `llm_infer`

在 `_infer_to_item(...)` 中：

1. `add_activity(activity_type=LLM_INFER, status=STARTED, title="推理", ...)`
2. 主推理走 `llmService.infer_stream(...)`
3. `on_progress` 中持续 `update_activity_progress(...)`
4. 成功时 `update_activity_progress(status=SUCCEEDED, ...)`
5. 失败时 `update_activity_progress(status=FAILED, error_message=..., ...)`

#### `compact`

在 `_execute_compact(...)` 中：

1. `add_activity(activity_type=COMPACT, status=STARTED, title="压缩上下文", ...)`
2. 执行 `compact.compact_messages(...)`
3. 成功/失败分别 `update_activity_progress(status=SUCCEEDED, ...)` / `update_activity_progress(status=FAILED, error_message=..., ...)`

`compact_stage` 写入 `metadata.compact_stage`。

#### `tool_call`

在 `_run_tool_to_item(...)` 中：

1. `add_activity(activity_type=TOOL_CALL, status=STARTED, title="调用工具", metadata.tool_name=...)`
2. 执行 `tool_registry.execute_tool_call(...)`
3. 根据结果调用 `update_activity_progress(status=SUCCEEDED / FAILED, ...)`

### 7.5 overflow retry 的活动表达

当第一次推理因 context overflow 失败时：

1. 第一条 `llm_infer` 记录更新为 `failed`
   - `error_message` 标注 overflow
   - `metadata.error_kind = "context_overflow"`
2. 创建一条 `compact` 记录
3. compact 成功后，再创建新一条 `llm_infer` 记录

因此前端可直接从活动时间线看出“失败推理 -> compact -> 重试推理”的完整过程。

### 7.6 `activate / idle` 的活动表达

当 Agent 从等待状态进入执行期时：

1. 先更新当前状态为 `ACTIVE`
2. 广播 `AGENT_STATUS_CHANGED`
3. 调用 `add_activity(activity_type=ACTIVATE, status=SUCCEEDED, ...)`
4. 广播对应的 `AGENT_ACTIVITY_CHANGED`

当 Agent 完成当前处理并回到等待期时：

1. 更新当前状态为 `IDLE`
2. 广播 `AGENT_STATUS_CHANGED`
3. 调用 `add_activity(activity_type=IDLE, status=SUCCEEDED, ...)`
4. 广播对应的 `AGENT_ACTIVITY_CHANGED`

这样前端既能用状态通道判断“当前活跃/休眠”，又能在活动时间线中看到何时发生过切换。

---

## 8. REST API 设计

位置建议：新增 `src/controller/activityController.py`，并在 `route.py` 注册。

### 8.1 查询接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/agents/{agent_id}/activities.json` | 查询某个 Agent 的活动记录 |
| `GET` | `/teams/{team_id}/activities.json` | 查询某个 Team 的活动记录 |
| `GET` | `/activities.json?room_id={room_id}` | 按 `metadata.room_id` 过滤活动记录 |

### 8.2 返回格式

```json
{
  "activities": [
    {
      "id": 1025,
      "agent_id": 5,
      "team_id": 1,
      "activity_type": "llm_infer",
      "status": "started",
      "title": "推理",
      "detail": "整理最终答复",
      "started_at": "2026-04-07T20:15:33+08:00",
      "finished_at": null,
      "duration_ms": null,
      "error_message": null,
      "metadata": {
        "room_id": 12,
        "current_completion_tokens": 128
      }
    }
  ]
}
```

排序约定：

- 默认按 `id desc`
- 前端若要以“旧 -> 新”展示时间线，可自行反转

---

## 9. 持久化与启动恢复

V11 的活动记录**不需要**像 Room/History 那样在启动时预加载到内存中参与恢复执行。

原因：

- 活动记录只用于查询与展示
- 它不是运行态真源，不参与调度恢复

因此：

- `persistenceService.startup()` 无需把 `agent_activities` 载入运行时对象
- 查询接口直接走 DAL 读取 SQLite
- WebSocket 只广播启动后新产生的活动事件

这可以避免把观测数据反向耦合进执行态。

---

## 10. 与现有模块的关系

### 10.1 保持不变

- `ChatRoom` 的状态机逻辑
- `GtAgentHistory` / `HistoryUsage`
- `messageBus` 的基础 publish/subscribe 机制
- `AGENT_STATUS_CHANGED` 作为 Agent 当前状态广播的职责

### 10.2 需要新增或改造

| 文件 | 改动 |
|------|------|
| `src/constants.py` | 新增 `AgentActivityType`、`AgentActivityStatus`、`AGENT_ACTIVITY_CHANGED` |
| `src/model/dbModel/gtAgentActivity.py` | 新增 ORM 模型 |
| `src/dal/db/gtAgentActivityManager.py` | 新增 DAL Manager |
| `src/service/agentActivityService.py` | 新增活动记录服务 |
| `src/service/llmService.py` | 新增 `infer_stream(...)` |
| `src/util/llmApiUtil/client.py` | 支持流式 chunk 回调 |
| `src/service/agentService/agentTaskConsumer.py` | 在 `ACTIVE / IDLE` 切换点补写 `activate / idle` 活动记录 |
| `src/service/agentService/agentTurnRunner.py` | 为 infer/tool/compact 接入活动记录 |
| `src/controller/wsController.py` | 订阅并广播 `agent_activity` |
| `src/controller/activityController.py` | 新增活动记录查询接口 |
| `src/route.py` | 注册新的 REST 与 WS 事件支持 |

---

## 11. 风险与边界

1. **流式 provider 差异**：不同 OpenAI-compatible 服务未必在 chunk 中提供 usage，需要本地估算兜底。
2. **高频更新压力**：流式推理期间若每个 chunk 都写库并广播，可能造成高频 IO。Step 1 建议引入简单节流：
   - 例如每累计 N 个 chunk 或每 200ms 刷新一次活动记录
3. **SQLite JSON 过滤能力有限**：`room_id` 在 `metadata` 中查询足够灵活，但过滤效率不如独立列；Step 1 先接受这一取舍。
4. **顺序语义依赖单库自增 `id`**：Step 1 默认以当前 SQLite 的自增 `id` 作为活动顺序依据；若未来走多实例、多库或异步归并存储，需要重新审视全局有序性与跨实例时间线合并策略。

---

## 12. 实施顺序建议

1. 新增常量、ORM 模型和 DAL Manager
2. 实现 `agentActivityService`
3. 扩展 `wsController` 与活动查询接口
4. 在 `agentTaskConsumer` 中补齐 `ACTIVE / IDLE` 到 `activate / idle` 的活动记录链路
5. 在 `AgentTurnRunner` 中先接入 `tool_call` / `compact` / 非流式 `llm_infer`
6. 扩展 `llmService + llmApiUtil` 的流式推理回调
7. 接入 `llm_infer` 的实时 token 更新
8. 补充单元测试、集成测试和 WebSocket 测试
