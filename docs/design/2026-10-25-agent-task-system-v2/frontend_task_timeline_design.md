# 任务详情事件时间线 - 前端专项设计

> **所属里程碑**：V27 · Agent 任务系统 v2（对应 step3 任务 8）
> **文档定位**：前端改造专项设计。后端字段与接口契约在此声明，由后端实现；本文只覆盖 `frontend/` 侧。
> **关联文档**：[step1_product.md](./step1_product.md) · [step2_technical.md](./step2_technical.md) · [step3_tasks.md](./step3_tasks.md)
> **定稿记录**：2026-10-27，实时刷新采用**方案 B**（事件级推送 `TASK_EVENT_APPENDED`），见 §2.3。

---

## 1. 背景与目标

v2 把任务从「单表覆盖写入」改为「状态机 + 不可变事件时间线」，事件（创建 / 快照 / 流转 / 评论）落在 `agent_task_events`。后端工具层已提供 `get_task_detail` 与 `list_task_events`，但 Web 前端仍停留在 v1 口径：

- 详情弹窗 `AgentTaskDetailModal.vue` 展示 `result` / `block_reason` 两个**已废弃列**（后端 ORM 已停止映射 ⇒ 永远空白）；
- 任务域字段仍是 `manager_id`（后端已改名为 `reviewer_id`）⇒ 验收人一栏永久空白且不报错（技术设计 §7-5「静默降级」）；
- **时间线完全没有入口**，Agent 之间的流转理由、打回原因、协作评论在 UI 上不可见。

**目标**：在**任务详情弹窗**内呈现完整事件时间线（流转 + 评论 + 创建/迁移快照），并同步 v2 字段口径。

**非目标**：任务看板 / 甘特图（二期 Backlog）；前端发起评论或流转操作（本期只读展示）；`TASK_ASSIGNED` 定向唤醒（本次非目标）。

---

## 2. 后端接口契约

> **已落地**：`src/controller/taskController.py` + `src/route.py`。两个端点都是只读，
> 复用 `taskService.get_task` / `list_task_events`，**统一传 `truncate_content=False`**（见 §2.1 末）。

### 2.1 任务详情

```http
GET /tasks/<task_id>.json
```

响应复用 `taskService.get_task()` 现有返回体，**不新增拼接字段**；任务不存在 ⇒ 400 + `task_not_found`：

```json
{
  "success": true,
  "task": {
    "id": 1, "team_id": 2, "title": "...", "description": "...",
    "assignee_id": 11, "creator_id": 10, "reviewer_id": 12,
    "status": "REVIEWING", "priority": "HIGH",
    "parent_id": null, "depends_on": [3], "room_id": null,
    "created_at": "2026-10-25 10:12:00.123456",
    "updated_at": "2026-10-25 10:30:00.123456",
    "depends_on_details": [{ "task_id": 3, "title": "接口设计", "status": "DONE" }],
    "events": [ /* 首屏事件，按 id 降序，最多 EVENT_TIMELINE_LIMIT=20 条 */ ],
    "events_total": 42,
    "last_note": "重构已就绪，PR #42",
    "current_block_reason": null
  }
}
```

> ✅ **`content` 截断口径（已解决）**：`get_task` / `list_task_events` 新增关键字参数 `truncate_content`，
> **默认 True 保持面向 Agent 的摘要口径**（工具层不变，超过 `EVENT_CONTENT_MAX_CHARS` 仍截成 `…(已截断)`），
> **Web 端点显式传 False** 返回完整原文 —— 与 §2.3 推送内容一致，不会出现「刚推来完整、刷新一下就变截断」。
> 用例：`tests/integration/test_task_events/test.py::TestTaskEventFullContent`、`tests/api/test_task_controller/test.py`。

### 2.2 事件分页（游标）

```http
GET /tasks/<task_id>/events.json?limit=20&before_id=87
```

响应复用 `taskService.list_task_events()`：

```json
{ "success": true, "events": [ /* 按 id 降序 */ ], "count": 20, "total": 42 }
```

- `before_id` 省略 → 取最新一页；向前翻页时传当前已加载的**最早一条** `event.id`。
- 事件对象结构：`{ id, task_id, actor_id, from_status, to_status, content, created_at, updated_at }`；`from_status`/`to_status` 为 `null` 表示该侧为空。
- 时间字段由 `jsonUtil` 序列化为 `%Y-%m-%d %H:%M:%S.%f`（**空格分隔、6 位微秒、本地时区**），前端解析须容错（见 §7）。

> 若后端选择不加 `/tasks/<id>.json`，前端可退化为「列表项 + 事件分页接口」两段式：任务静态字段取自列表，时间线与 `current_block_reason` 由事件流前端推导。推荐仍按上述契约实现，以拿到 `depends_on_details`（依赖标题）与后端统一口径的派生字段。

### 2.3 事件推送（方案 B：`TASK_EVENT_APPENDED`）

现状（HEAD `14e2a13a`）：只有 `create_task` 与 `update_task` 的**流转分支**会 publish，评论、系统推进、认领置位都不发，且 payload 只有任务快照、不含事件内容 ⇒ 时间线无法实时。

**定稿方案 B**：新增单一推送主题 `TASK_EVENT_APPENDED`，**只要往 `agent_task_events` 写了一条事件就推一条**，与「事件表是唯一真实来源」保持一致。

```python
# src/constants.py
class MessageBusTopic(EnhanceEnum):
    ...
    TASK_EVENT_APPENDED = auto()   # 任务事件追加；payload: team_id(int), task(GtAgentTask), task_event(dict)
```

payload 三要素：

| 字段 | 类型 | 说明 |
|---|---|---|
| `team_id` | `int` | 事件表本身**没有** `team_id`，必须由 service 侧补上，前端按团队过滤 |
| `task` | `GtAgentTask` | 推送时刻的任务快照，供前端 upsert 列表项（等价于原 `TASK_CHANGED` 的 `task`） |
| `task_event` | `dict` | `_serialize_event(event, truncate_content=False)`，**不截断**——100 字符截断是面向 Agent 摘要的口径，UI 要完整原文。**字段名不能叫 `event`**（原因见下） |

**五个写入点全部发**（与「写入入口静态检查」的入口清单一一对应）：

| # | 写入点 | 层级 | 事件性质 | 当前是否 publish |
|---|---|---|---|---|
| 1 | `taskService.create_task` | service | create | ❌ 需补 |
| 2 | `taskService.update_task` 流转分支 | service | transition | ⚠️ 只发 `TASK_CHANGED` |
| 3 | `taskService.update_task` 评论分支 | service | comment | ❌ 需补 |
| 4 | `taskService.auto_progress_dependents` | service | transition(system) | ❌ 需补 |
| 5 | `taskService.claim_task` | service | transition(system) | ❌ 需补 |

#### 分层边界：DAL 只保存，事件由 service 构造

两条约束叠加：

1. **`dal` 不得 publish** —— 四层架构规定 `dal` 只能依赖 `model` / `util`，而 `messageBus` 在 `service` 层，DAL 里 publish 会构成 `dal → service` 反向依赖。目前 `src/dal/db/` 下没有任何 `service` 导入，这条不能破。
2. **`dal` 不得决定「记什么事件」** —— 现有的 `create_task` / `update_task_status_with_event` 把 `actor_id`、`from_status` / `to_status`、系统文案全写死在 DAL 里，这是业务判断跑到了数据层。

因此调整 DAL：**只保留「保存原语 + 事务边界」**，事件的构造全部上移到 service。`gtAgentTaskEventManager.append_event` 保持原样（它本就是纯粹的「插入一行事件」，参数全由调用方给，也正好是设计 §8.3 静态检查要求的唯一写入点）。

```python
# src/dal/db/gtAgentTaskManager.py —— 只剩保存原语
async def save_task(
    task: GtAgentTask,
    *,
    force_insert: bool = False,
    only: list | None = None,
) -> None:
    """保存任务（插入 / 按字段局部更新）。**不自带事务**，调用方用公共 `atomic()` 包住。"""
    ...
```

事务边界放在**公共位置** `model/dbModel/base.py`（所有模型共用同一个 `_database_proxy`，
事务本身也不属于某个具体 Manager）：

```python
# src/model/dbModel/base.py
@asynccontextmanager
async def atomic() -> AsyncIterator[None]:
    """跨表写操作的公共事务边界。

    事务**提交后**再做副作用（如 publish 事件）——回滚时副作用不应发生。
    """
    async with _database_proxy.aio_atomic():
        yield
```

`gtAgentTaskEventManager.append_event` 补一句说明：**不自带事务，由调用方的 `atomic()` 控制**，避免以后有人加内层事务把语义搞复杂。

service 侧统一走一个 helper 推送（事务提交后才发）：

```python
# src/service/taskService.py
def _publish_event_appended(team_id: int, task: GtAgentTask, event: GtAgentTaskEvent) -> None:
    """事件写入后统一推送（service 层唯一出口；dal 不碰 messageBus）。"""
    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT
    _mb.publish(
        _MBT.TASK_EVENT_APPENDED,
        team_id=team_id,
        task=task,
        task_event=_serialize_event(event, truncate_content=False),
    )
```

五个写入点统一成「`atomic()` 内保存任务 + 追加事件 → 块外 publish」。
流转的三处（`update_task` / `auto_progress_dependents` / `claim_task`）收敛到一个私有入口
`taskService._transition_with_event(...)`，避免三份重复的事务 + 推送样板：

```python
# 创建
async with atomic():  # model.dbModel.base.atomic
    await gtAgentTaskManager.save_task(task, force_insert=True)
    event = await gtAgentTaskEventManager.append_event(
        task.id, creator_id, from_status=None, to_status=initial_status, content='',
    )
_publish_event_appended(team_id, task, event)

# 流转（三处写入点共用；评论分支只有 append_event，没有 save_task）
async def _transition_with_event(team_id, task, *, actor_id, from_status, to_status, content=''):
    async with atomic():  # model.dbModel.base.atomic
        task.status = to_status
        await gtAgentTaskManager.save_task(task, only=[GtAgentTask.status])
        event = await gtAgentTaskEventManager.append_event(
            task.id, actor_id, from_status=from_status, to_status=to_status, content=content,
        )
    _publish_event_appended(team_id, task, event)
    return task
```

- **publish 必须在 `async with` 之外**：`append_event` 用裸 `aio_save`，会跟随外层事务，回滚时任务与事件一起消失；publish 放在块外，就不会出现「回滚了还推了一条」。
- **`team_id` 来源**：`agent_task_events` 表没有 `team_id`，直接用 `task.team_id`（各写入点都握着任务对象）。
- **自动推进 / 认领置位**：同样调用 `_transition_with_event(..., actor_id=SpecialAgent.SYSTEM.value, ...)`，只是 `content` 换成系统文案。

**测试（已落地）**：单元测试用 `atomic` 替身 + `save_task`/`append_event` 断言；
`tests/unit/service/test_task_service.py` 覆盖五个写入点的推送次数与顺序
（创建 ⇒ `TASK_CREATED` + `TASK_EVENT_APPENDED`；流转 ⇒ `TASK_EVENT_APPENDED` + `TASK_CHANGED`；
评论 ⇒ 仅 `TASK_EVENT_APPENDED`；失败 ⇒ 不推）。

**与现有两个主题的关系**：`TASK_CREATED` / `TASK_CHANGED` 目前只有 `wsController` 一个订阅者，没有其他后端消费者。推荐**直接由 `TASK_EVENT_APPENDED` 取代**（删除两条 publish、保留枚举值与 `wsController` 的映射一版做兼容，下个里程碑清掉）；否则流转路径会同时推两条，前端必须按 `event.id` 去重。**本文按「新前端只订阅 `TASK_EVENT_APPENDED`」设计**，旧主题是否保留由后端定。

`wsController.py` 需要同步两处：`_WS_TOPICS` 追加 `TASK_EVENT_APPENDED`；`_on_event` 增加类型名映射。

```python
# 直接在 publish 时就把业务字段命名为 task_event，wsController 无需改名
if msg.topic == MessageBusTopic.TASK_EVENT_APPENDED:
    payload["event"] = "task_event_appended"   # 消息类型（既有约定）
    # payload["task_event"] / payload["task"] / payload["team_id"] 原样透传
```

> 这是本方案落地时最容易踩的坑：`wsController._on_event` 用 `payload["event"]` 表示「消息**类型**」，若业务事件字段也叫 `event`，`dict` 赋值会把它覆盖成字符串 `"task_event_appended"`，前端永远拿不到事件对象。**因此 publish 时字段名一律用 `task_event`**。

**对 step2 §7-8 的修订**：原文「系统自动推进 / 认领置位 **不 publish `TASK_CHANGED`**、不触发任何唤醒 / 通知」需要拆成两件事——
- **唤醒 / 通知**：仍然不发（不变，唤醒链路零改动）；
- **可见性**：改为**发 `TASK_EVENT_APPENDED`**（仅一条 WS 消息，不进调度表、不唤醒任何 Agent）。

§7-8 原「上述两类系统状态变更不触发前端实时刷新，需下次拉取方可见」的边界随之作废。step2 需要相应补一条修订记录（后端落地时一并改）。

---

## 3. 数据模型改造

### 3.1 `frontend/src/types.ts`

```ts
export type AgentTaskEventKind = 'create' | 'snapshot' | 'transition' | 'comment';

export interface AgentTaskEvent {
  id: number;
  task_id: number;
  actor_id: number;
  from_status: AgentTaskStatus | null;
  to_status: AgentTaskStatus | null;
  content: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentTaskDependencySummary {
  task_id: number;
  title: string;
  status: AgentTaskStatus | 'UNKNOWN';
}

export interface AgentTask {
  // … 既有字段
  reviewer_id: number | null;   // ← 原 manager_id
  // result / block_reason      ← 删除
  // 以下仅详情接口返回，列表接口不带，故可空
  last_note?: string;
  current_block_reason?: string | null;
  depends_on_details?: AgentTaskDependencySummary[];
  events?: AgentTaskEvent[];
}
```

`AgentTaskStatus`（`TODO|PENDING|IN_PROGRESS|REVIEWING|ON_HOLD|DONE|CANCELLED`）与 `AgentTaskPriority` 不变。

> ⚠️ 任务域改名**不得**波及部门域：`types.ts` 中 `DeptTreeNode.manager_id`、`utils.ts` 的 `findDepartmentPath` / `isDepartmentLeader`、`TeamTreeEditor.vue`、`ConsoleChatPanel.vue` 全部保持原样。

### 3.2 `frontend/src/realtime/eventNormalizer.ts`

`normalizeAgentTask` 与 `api.ts` 内的同名函数是**两份独立实现**（WS 推送与 HTTP 响应各一份），必须同步改：`manager_id` → `reviewer_id`，删除 `result` / `block_reason`。否则 WS 推送的任务对象与拉取的任务对象字段不一致，会表现为「刷新前空白、刷新后正常」的随机现象。

同时新增 `task_event_appended` 的解析（方案 B）：

```ts
// 事件联合类型追加
| { type: 'task_event_appended'; teamId: number; task: AgentTask; event: AgentTaskEvent }

// 解析分支（与 task_created / task_changed 并列）
if (eventType === 'task_event_appended') {
  const task = normalizeAgentTask(raw.task);
  const event = normalizeAgentTaskEvent(raw.task_event); // 见 §2.3 键冲突说明
  if (!task || !event) return null;
  return { type: 'task_event_appended', teamId: task.team_id, task, event };
}
```

`normalizeAgentTaskEvent` 同样需要容错：`from_status` / `to_status` 用 `null` 表示空，非枚举值一律降级为 `null`（不要因为后端多出一个新状态就让整个事件被丢弃）；`id` 非有限数则丢弃整条事件。

---

## 4. 组件结构与渲染职责

### 4.1 职责划分

| 组件 | 职责 |
|---|---|
| `AgentTaskDetailModal.vue` | **唯一的时间线渲染点**（agent 侧栏与 console 任务树共用，两处入口天然覆盖）；自行拉取详情与事件分页；任务域的 status / priority / 事件类型翻译由组件内部完成 |
| `AgentTaskPanel.vue` | 传入任务、actor 名称解析函数、房间/时间标签 |
| `ConsoleTaskTreePanel.vue` | 同上 |
| `AgentTaskCard.vue` / `ConsoleTaskTreeNode.vue` | 仅列表卡片：`managerLabel` → `reviewerLabel` |

**为什么由 Modal 自取数据**：两个父组件都已有各自的 `tasks` 来源（agent 侧栏走独立请求、console 走 `runtimeStore`），若把 `events` 作为 prop 传入，两处都要重复分页、合并、滚动保持逻辑。Modal 内聚一份即可。

### 4.2 Props 调整

```ts
defineProps<{
  taskId: number | null;              // 原 task: AgentTask | null（改为按 id 取，见 §6 实时刷新）
  createdAtLabel: string;
  creatorLabel: string;
  assigneeLabel: string;
  reviewerLabel: string;              // 原 managerLabel
  roomLabel: string;
  actorLabel: (id: number) => string; // 时间线 actor 解析；父组件用自己的 agentLabels
}>();
```

- `priorityLabel` / `statusLabel` 两个 prop **移除**：Modal 已 `useI18n()`，事件里还要显示历史状态，统一由组件内部 `t('agent.taskStatus.' + s)` 翻译，避免「当前状态翻译在父、历史状态翻译在子」两套口径。
- `actorLabel` 传函数而非 Map：父组件的 fallback 文案不同（`notSet` vs `none`），但时间线一律用 `#<id>`。
- `-2`（`SpecialAgent.SYSTEM`）与 `-1`（`SpecialAgent.OPERATOR`）**在 Modal 内拦截**，不交给父组件（它们不是团队成员）。

### 4.3 弹窗结构（新增/调整部分）

```
任务详情
├─ 标题 / 关闭
├─ 徽章：优先级 · 当前状态
├─ 网格：任务 ID · 创建时间 · 创建人 · 处理人 · 验收人(改名) · 房间
├─ 【当前搁置原因】            ← 仅 status===ON_HOLD 时显示，醒目置于描述之前
├─ 任务说明
├─ 依赖任务                   ← 由 #id 列表升级为「#3 接口设计（已完成）」，UNKNOWN 时显示「(已删除)」
└─ 事件时间线                  ← 新增（替换原 result / block_reason 两个区块）
   ├─ 头部：标题 + 「共 N 条」
   ├─ [加载更早的事件]         ← 仅在还有更早事件时显示（置顶）
   └─ 事件条目 × N（升序）
```

---

## 5. 事件条目设计

### 5.1 类型判定（四格组合，纯前端推导）

| `from_status` | `to_status` | kind | 渲染 |
|---|---|---|---|
| `null` | 非空，`content` 以 `【系统迁移自动生成】` 开头 | `snapshot` | 「历史迁移快照」徽标（虚线/灰底）+ 说明 |
| `null` | 非空，其余 | `create` | 「创建」徽标 + 初始状态 |
| 非空 | 非空 | `transition` | `旧状态 → 新状态` + 说明 |
| `null` | `null` | `comment` | 「评论」徽标 + 正文（无状态变化行） |

> `非空 → null` 在服务端已被 `append_event` 断言拒绝，前端**不需要**为这一格写分支。

### 5.2 条目布局

```
●  2026-10-25 10:12:00            系统
   待开始 → 进行中
   执行人认领，系统自动置为 IN_PROGRESS
│
○  2026-10-25 10:20:31            阿张
   进行中 → 待验收
   重构已就绪，PR #42
│
○  2026-10-25 10:30:02            小李
   评论
   建议排查 SameSite Cookie 配置
```

- 左侧竖线 + 节点圆点（`transition` 实心、`comment` 空心、`snapshot` 虚线圆），与既有 `AgentActivityItem` 的视觉语言一致。
- `actor_id === -2` → 「系统」，弱化色（`var(--muted)`）；`-1` → 「操作者」；其余走 `actorLabel(id)`。
- `content` 为空（正常创建事件）→ 不渲染说明行；`snapshot` 的 `content` **原样展示**（后端明确「前缀暂不剥离」，前端不擅自裁剪，避免与 `last_note` 口径不一致）。
- 文本 `white-space: pre-wrap; word-break: break-word`，与详情区块一致。

### 5.3 排序与分页

- 后端恒按 `id` **降序**返回；UI `sortAscending` 后**升序**渲染（最新在底部，与活动日志面板一致）。
- 首屏：`limit = 20`（与后端 `EVENT_TIMELINE_LIMIT` 对齐）；滚动/点击「加载更早」时传 `before_id = 已加载事件的最小 id`。
- `total` 来自事件分页接口的 `total`；`hasMore = 已加载条数 < total`。
- 若放在可滚动容器内实现「滚到顶部自动加载」，沿用 `AgentActivityPanel.loadOlderActivities()` 的滚动位置保持方案（记录 `scrollHeight` / `scrollTop`，插入后回填）。**本期建议先用显式「加载更早的事件」按钮**，实现简单、无滚动抖动风险；自动加载留待二期。

---

## 6. 加载、实时刷新与错误态

### 6.1 打开弹窗

```ts
watch(() => props.taskId, async (id, _prev, onCleanup) => {
  if (id === null) { reset(); return; }
  let cancelled = false;
  onCleanup(() => { cancelled = true; });
  const [detail, page] = await Promise.all([
    getTaskDetail(id),                                  // GET /tasks/<id>.json
    getTaskEventsPage(id, { limit: 20 }),               // GET /tasks/<id>/events.json
  ]);
  if (cancelled) return;
  // detail.task 覆盖列表项（拿到 reviewer_id / last_note / current_block_reason / depends_on_details）
  // page.events + page.total 初始化时间线
});
```

- 请求竞态用 `cancelled` 标记（`onCleanup`）丢弃过期响应；父组件切换选中任务时不串数据。
- 详情接口失败但事件接口成功 → 仍渲染时间线，其余字段回落到列表项，并在时间线区域顶部显示轻量错误；反之事件失败 → 其余区块正常，时间线区域显示「事件加载失败」+「重试」按钮。

### 6.2 实时刷新（方案 B，事件级推送）

`runtimeStore` 目前只在 `task_created` / `task_changed` 时 upsert 任务列表，**时间线拿不到新事件**。方案 B 下前端统一订阅 `task_event_appended`：

```ts
// eventNormalizer.ts：新增一种 FrontendRealtimeEvent
// 注意：Wire 上业务事件的字段名是 task_event（见 §2.3 末的键冲突说明），
//      归一化后前端内部统一叫 event。
| { type: 'task_event_appended'; teamId: number; task: AgentTask; event: AgentTaskEvent }
```

三处响应，各司其职：

1. **任务列表（全局）**：`runtimeStore.applyRealtimeEvent` 收到后，照旧 upsert 团队任务列表 + 追加到 `teamTaskEventsState[taskId]`（见 §6.3），供其他正在看这个任务的弹窗/面板使用。
2. **弹窗（已打开且正是该任务）**：Modal 订阅 `subscribeRealtimeEvents`，`event.task_id === props.taskId` 时**直接 append**，无需再请求接口——这正是 B 相对 A 的核心收益（评论也能即时可见，且省一次往返）。
3. **未打开的任务**：只进 store，不做额外处理。

**幂等与顺序**：按 `event.id` 去重（本地已有则丢弃）；插入后按 `id` 升序重排。因为 WS 与 HTTP 首屏请求存在竞态（首屏还在飞、推送先到），合并逻辑必须是「按 id 合并」而不是「覆盖数组」：

```ts
function mergeEvents(existing: AgentTaskEvent[], incoming: AgentTaskEvent[]): AgentTaskEvent[] {
  const byId = new Map(existing.map((e) => [e.id, e]));
  for (const e of incoming) byId.set(e.id, e);   // 同 id 以新到的为准（内容不可变，等价）
  return [...byId.values()].sort((a, b) => a.id - b.id);
}
```

**断线重连补偿**：WS 断开期间的事件不会补发。`wsClient` 的 `connectionState` 转回 `connected` 时，若弹窗打开中，重新拉取一次首屏事件（`limit = 已加载条数或 20 的较大者`）并按 id 合并——沿用 `AgentActivityPanel` 里已有的「重连后 reload」模式。

**父组件的 `selectedTask` 快照问题**：两个父组件的 `selectedTask` 都是打开瞬间的 `ref` 快照，任务字段变化不会流进弹窗。改造：

```ts
const selectedTaskId = ref<number | null>(null);
const selectedTask = computed(() =>
  selectedTaskId.value === null ? null : tasks.value.find((t) => t.id === selectedTaskId.value) ?? null,
);
```

- `ConsoleTaskTreePanel`：`tasks` 已是 `runtimeStore` 的 computed ⇒ 自动跟随。
- `AgentTaskPanel`：`tasks` 是本地 `ref`，改为从 `runtimeStore` 派生同一份数据（或订阅 `task_event_appended` 手动 upsert 到本地列表），使两侧行为一致。

### 6.3 事件是否要进 `runtimeStore`

**建议进**：新增 `teamTaskEventsState: Record<number, AgentTaskEvent[]>`（按 `task_id` 索引，单任务上限保留最近 100 条，与 `MAX_AGENT_ACTIVITY_ITEMS` 对齐）。

理由：
- 同一个任务可能在 Agent 侧栏和 Console 任务树**同时打开**，共享一份数据可避免两次请求与两套合并逻辑；
- 关闭再打开弹窗时可直接命中缓存，只有「加载更早」才走接口；
- 断线重连后的补拉也只需更新这一处。

代价是 RuntimeStore 多一份状态，需在 `clearRuntimeStore()` 里一并清空。

---

## 7. 时间解析（务必修正）

后端 datetime 序列化为 `2026-10-25 10:12:00.123456`（空格分隔、6 位小秒）。现有 `formatTaskDateTime()` 用 `new Date(value)`：

- Chrome 可容忍，但会**丢弃微秒**；Safari / 部分 WebView 直接得到 `Invalid Date` ⇒ 时间显示为空（当前任务卡片与详情弹窗已存在该隐患）。

新增共享工具（`frontend/src/utils.ts`）：

```ts
/** 解析后端 datetime（空格分隔 + 微秒）；解析失败返回 null。 */
export function parseBackendDateTime(value: string | null | undefined): Date | null {
  if (!value) return null;
  const normalized = value.trim().replace(' ', 'T').replace(/(\.\d{3})\d+$/, '$1');
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatBackendDateTime(value: string | null | undefined): string {
  const date = parseBackendDateTime(value);
  return date ? `${date.toLocaleDateString()} ${date.toLocaleTimeString()}` : '';
}
```

事件条目与既有任务卡片（`AgentTaskPanel` / `ConsoleTaskTreePanel` 的 `formatTaskDateTime`）统一改用该工具，顺带修掉 Safari 空白问题。

---

## 8. i18n（`zh-CN.json` / `en.json` 同步）

新增（`agent` 命名空间）：

| Key | zh-CN | en |
|---|---|---|
| `taskReviewerLabel` | 验收人 | Reviewer |
| `taskReviewer` | 验收：{id} | Reviewer: {id} |
| `taskEventsLabel` | 事件时间线 | Timeline |
| `taskEventsTotal` | 共 {count} 条 | {count} total |
| `taskEventsEmpty` | 暂无事件记录。 | No events yet. |
| `taskEventsLoadFailed` | 事件加载失败。 | Failed to load events. |
| `taskEventsLoadMore` | 加载更早的事件 | Load earlier events |
| `taskEventsLoadingMore` | 正在加载… | Loading… |
| `taskEventsRetry` | 重试 | Retry |
| `taskEventKind.create` | 创建 | Created |
| `taskEventKind.snapshot` | 历史迁移快照 | Migrated snapshot |
| `taskEventKind.transition` | 状态流转 | Status change |
| `taskEventKind.comment` | 评论 | Comment |
| `taskEventActorSystem` | 系统 | System |
| `taskEventActorOperator` | 操作者 | Operator |

删除：`taskResultLabel`、`taskManagerLabel`、`taskManager`（改名后不再引用）。
`taskBlockReasonLabel` 保留，语义收窄为「当前搁置原因」。

---

## 9. 兼容与回归注意事项

1. **前后端必须同批发布**：后端已停止返回 `result` / `block_reason`，前端若是旧版只会显示空白（不报错）；反之新前端对旧后端会拿不到 `reviewer_id` / `events`。按技术设计 §7-5 的要求一并发布。
2. **部门域零改动**：`GtDept.manager_id`、部门树、`TeamTreeEditor`、`ConsoleChatPanel`、`utils.ts` 的部门辅助函数、`teamSummary` 测试夹具全部不动。
3. **`taskTree.test.ts` / `api.test.ts` 夹具**中的任务对象需同步改字段（删除 `result` / `block_reason`，`manager_id` → `reviewer_id`），否则类型报错。
4. **WS 与 HTTP 两份 normalize 必须同时改**（§3.2），否则出现「部分路径字段缺失」。
5. **空数据不崩**：`events` 缺省、`depends_on_details` 缺省、`total` 缺省均需降级；旧任务（0015 结转）时间线首条为快照事件，`content` 带 `【系统迁移自动生成】` 前缀，属预期。
6. **推送与首屏的竞态**：`task_event_appended` 可能早于首屏 HTTP 响应到达，合并一律按 `event.id` 去重（§6.2），禁止直接 `events = incoming`。
7. **推送的 `content` 不截断**：`_serialize_event(..., truncate_content=False)`。若后端误用默认截断，UI 会显示 `…(已截断)` 而 `get_task_event` 又能取到全文，属于可自查的不一致。
8. **断线重连必须补拉**：WS 断开期间的事件不补发，`connectionState === 'connected'` 时重拉首屏并合并，否则时间线永久缺一段。
9. **若后端保留 `TASK_CHANGED` 双发**：前端只处理 `task_event_appended`，且两条消息都会带同一 `task` 快照 ⇒ 必须按 `event.id` 去重，否则时间线出现重复条目。最干净的做法是后端删掉旧的两条 publish。

---

## 10. 任务拆分

| # | 任务 | 文件 | 依赖 |
|---|---|---|---|
| F1 | 类型与字段口径：`reviewer_id`、删 `result`/`block_reason`、新增事件/详情类型 | `types.ts` | 后端字段就绪 |
| F2 | HTTP 层：`normalizeAgentTask` 改字段；新增 `getTaskDetail` / `getTaskEventsPage` | `api.ts` | 后端接口就绪 |
| F3 | WS 归一化同步 | `realtime/eventNormalizer.ts` | F1 |
| F4 | 时间解析工具 + 替换既有 `formatTaskDateTime` | `utils.ts`、两个 Panel | — |
| F5 | 详情弹窗：props 改版、删 result/block_reason 区块、依赖标题、搁置原因 | `AgentTaskDetailModal.vue` | F1 |
| F6 | 时间线渲染：四类事件条目 + 类型徽标 + actor 解析 | `AgentTaskDetailModal.vue`（或抽 `AgentTaskEventTimeline.vue`） | F5 |
| F7 | 时间线加载与分页（首屏 / 加载更早 / 错误重试 / 竞态丢弃） | 同上 | F6、F2 |
| F8 | 父组件接线：`selectedTaskId` 计算属性、`actorLabel`、`reviewerLabel` | `AgentTaskPanel.vue`、`ConsoleTaskTreePanel.vue`、`ConsoleTaskTreeNode.vue`、`AgentTaskCard.vue` | F1、F5 |
| F9 | i18n 增删 | `locales/zh-CN.json`、`locales/en.json` | F5、F6 |
| F10 | 实时：`task_event_appended` 类型 + 归一化 + store 落库 | `realtime/eventNormalizer.ts`、`realtime/runtimeStore.ts` | F1、后端 §2.3 |
| F11 | 实时：弹窗订阅推送、按 id 合并、重连补拉 | `AgentTaskDetailModal.vue`（或时间线组件） | F10、F7 |
| F12 | 测试：夹具更新 + 弹窗时间线渲染 + api 归一化 + 推送合并/去重 | `src/__tests__/api.test.ts`、`src/utils/__tests__/taskTree.test.ts`、`realtime/__tests__/runtimeStore.test.ts`、新增 `components/agent/__tests__/AgentTaskDetailModal.test.ts` | F1–F11 |

> 建议把时间线整体抽成 `frontend/src/components/agent/AgentTaskEventTimeline.vue`（props：`taskId`、`actorLabel`、`events`、`total`；emit：`load-more`），数据获取与合并留在 Modal。这样单测可以只挂载时间线组件，不必构造整个弹窗。

---

## 11. 验收标准

- [ ] 从 **Agent 侧栏**与 **Console 任务树**两个入口打开同一任务，详情弹窗展示一致的时间线。
- [ ] 四类事件（创建 / 迁移快照 / 流转 / 评论）视觉可区分；流转显示「旧状态 → 新状态」；评论不显示状态行。
- [ ] `actor_id = -2` 显示「系统」，`-1` 显示「操作者」，其余显示 Agent 名称，成员缺失时回退 `#<id>` 且不报错。
- [ ] 时间线升序排列；超过 20 条时可加载更早事件，加载后**滚动位置不跳变**，`共 N 条` 数字正确。
- [ ] 事件接口失败时，弹窗其余区块仍可用，时间线区域显示错误与重试。
- [ ] 验收人一栏正确显示 `reviewer_id` 对应成员（不再是空白）；依赖栏显示依赖标题与状态。
- [ ] `status === ON_HOLD` 时显示当前搁置原因，离开该状态后消失。
- [ ] 任务卡片与详情的时间在 Safari 下正常显示（不再空白）。
- [ ] **实时**：任务详情打开时，另一侧「追加评论」→ 时间线**即刻**出现该评论（不刷新页面）；系统推进 / 认领置位同样即时出现。
- [ ] **实时幂等**：同一条推送重复投递、或推送早于首屏响应到达，时间线都不出现重复条目。
- [ ] **断线重连**：断开 WS 期间产生的评论，在重连后自动补齐。
- [ ] 全仓 `frontend/src` 内**任务域**不再出现 `manager_id` / `result` / `block_reason`；**部门域**的 `manager_id` 原样保留。
- [ ] `npm run build`（含 `vue-tsc --noEmit`）无类型错误；`npm run test:run` 全绿。
