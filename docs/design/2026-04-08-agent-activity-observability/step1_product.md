# V11: Agent 活动记录与运行态可观测性 - 产品文档

## 目标

在现有多 Agent 后端服务能力的基础上，引入独立于消息历史的 **Agent 活动记录（Activity Record）** 机制。让系统不仅能产出“聊天结果”，还能持续暴露 Agent 在运行过程中“正在做什么、做到哪一步、是否出错、耗时多久”。

V11 的目标不是改变 Agent 的调度或推理逻辑，而是补齐运行态可观测性：后端在 Agent 执行关键步骤时，自动创建活动记录、持续更新状态、持久化保存，并通过实时广播推送给前端，便于展示当前进度和定位问题。

本文档默认描述的是**后端服务能力**；TUI / Web 前端仅作为活动记录的消费方和展示载体。

---

## 功能特性

- **独立活动记录对象**：新增 `AgentActivityRecord`，与聊天室消息、Agent history 分离，专门描述运行中的行为事件。
- **关键步骤全覆盖**：至少覆盖大模型推理、工具调用、上下文压缩（compact）等核心运行环节；Agent 的调度等待不再建模为独立的 `scheduler_wait` 活动类型，而是继续通过“激活 / 休眠”状态通道表达当前运行态，并在切换时补充 `activate / idle` 活动记录；context overflow 的重试过程拆解为失败的 `llm_infer`、后续 `compact` 和重试 `llm_infer`。
- **流式 token 更新**：`llm_infer` 活动记录在流式响应过程中持续更新当前已收到的 token 数，前端可实时看到推理进度，而不是只在结束后看到一次性统计结果。
- **活动生命周期**：每条活动记录支持 `started / succeeded / failed / cancelled` 等状态，能够表达开始、成功、失败和中断。
- **实时状态广播**：后端通过 WebSocket 主动推送活动事件，前端无需轮询即可知道某个 Agent 当前是否在推理、调用工具或压缩上下文。
- **持久化活动日志**：活动记录写入独立存储，支持按 Agent、Team、时间窗口查询，并可按扩展元数据过滤房间等上下文，用于排障、回放和审计。
- **前端友好展示**：活动记录提供可直接展示的摘要文本与结构化元数据，前端可用来显示“正在推理”“调用工具中”“压缩上下文中”等运行态提示。
- **消息历史解耦**：活动记录不会进入 Agent 的对话上下文，不参与后续推理，避免为了排障而污染聊天历史。

---

## 用户价值

### 1. 让 Agent 不再像“黑盒”

此前系统只能看到最终消息，用户无法判断 Agent 此刻是在推理、等待调度、执行工具还是卡在 compact。V11 之后，运行中的每一步都可见。

### 2. 降低问题排查成本

当某个 Agent 长时间无响应、回复异常缓慢、工具调用失败或 compact 频繁触发时，用户和开发者可以通过活动记录快速定位故障点，而不是只能从最终错误反推原因。

### 3. 为前端提供实时运行态反馈

前端不再只能展示“最后一条聊天消息”，而是能在 Agent 工作过程中实时展示其当前动作，提升可理解性和可控感。

### 4. 为后续审计与统计打基础

活动记录天然适合作为后续运行分析的基础数据，例如统计某类 Agent 平均推理耗时、某个工具失败率、compact 触发频率等。

---

## 核心概念

### 活动记录（AgentActivityRecord）

活动记录是 Agent 在运行过程中产生的一条结构化日志。它不代表聊天消息，而代表一次“动作”。

建议包含以下核心字段：

| 字段 | 含义 |
|------|------|
| `id` | 活动记录唯一标识 |
| `team_id` | 所属 Team |
| `agent_id` | 所属 Agent |
| `activity_type` | 活动类型，如 `llm_infer`、`tool_call`、`compact`、`activate`、`idle` |
| `status` | 活动状态，如 `started`、`succeeded`、`failed`、`cancelled` |
| `title` | 面向前端展示的简短标题 |
| `detail` | 活动摘要或补充描述 |
| `error_message` | 失败原因 |
| `started_at` | 开始时间 |
| `finished_at` | 结束时间 |
| `duration_ms` | 执行耗时 |
| `metadata` | 扩展元数据，如 `room_id`、模型名、工具名、token 信息 |

### 活动类型

V11 Step 1 至少覆盖以下类型：

| 类型 | 说明 |
|------|------|
| `llm_infer` | 发起一次大模型推理 |
| `tool_call` | 调用一次工具 |
| `compact` | 执行上下文压缩 |
| `activate` | Agent 进入激活执行期 |
| `idle` | Agent 进入休眠等待期 |

说明：

- `overflow retry` 不作为独立 `activity_type`
- 当发生上下文溢出时，活动序列应表现为：一次失败的 `llm_infer` → 一次 `compact` → 一次新的 `llm_infer`
- 这样可以避免为控制流再单独引入一类概念重复的活动类型
- `scheduler_wait` 也不作为独立 `activity_type`
- Agent 是否在等待调度、等待事件或重新被唤醒，继续通过当前运行态中的 `active / idle` 表达
- 与此同时，每次从休眠进入执行期、或从执行期回到等待期时，还会分别补充一条 `activate / idle` 活动记录，形成可回放的时间线

### 推理活动的流式更新

`llm_infer` 是唯一需要在执行过程中持续刷新的活动类型。

当模型采用流式返回时，活动记录应支持以下更新节奏：

1. 推理开始时创建一条 `llm_infer + started` 记录
2. 在流式 chunk 持续到达时，持续更新该记录的当前 token 统计
3. 推理结束后，将该记录更新为 `succeeded` 或 `failed`

建议在 `metadata` 中维护以下 token 相关字段：

| 字段 | 含义 |
|------|------|
| `estimated_prompt_tokens` | 请求发出前估算的 prompt token |
| `current_completion_tokens` | 当前已流式收到的 completion token 数 |
| `current_total_tokens` | 当前累计 token 数 |
| `final_prompt_tokens` | 推理结束后模型返回的最终 prompt token |
| `final_completion_tokens` | 推理结束后的最终 completion token |
| `final_total_tokens` | 推理结束后的最终总 token |

说明：

- `current_*` 字段用于推理过程中的实时刷新
- `final_*` 字段用于推理完成后的最终结算
- 若底层模型服务无法在流式阶段提供完整 usage，则至少要保证 `current_completion_tokens` 可随流持续增长

### Agent 运行态切换

V11 中，Agent 的“当前运行态”和“历史活动记录”是两条并行通道：

- 当前运行态用于表达 Agent 此刻是否处于活跃执行期
- 活动记录用于表达 Agent 发生过哪些动作，其中也包含运行态切换本身

当前运行态至少包含以下两种状态：

| 状态 | 说明 |
|------|------|
| `active` | Agent 已被激活，正在处理 turn、执行工具或推进流程 |
| `idle` | Agent 当前休眠，处于等待调度、等待新消息或等待下一次触发 |

对应地，活动时间线中还会出现以下类型：

| 活动类型 | 说明 |
|------|------|
| `activate` | Agent 从等待期进入执行期 |
| `idle` | Agent 从执行期回到等待期 |

说明：

- 当前运行态仍用于表达“Agent 当前是否活跃”，前端可以据此展示“运行中 / 休眠中”标记
- `activate / idle` 活动记录用于表达“什么时候发生过状态切换”，便于事后回放与排障
- 两者是独立但并行的表达：一个回答“现在是什么状态”，一个回答“何时切换过状态”

### 活动状态

| 状态 | 含义 |
|------|------|
| `started` | 已开始，尚未完成 |
| `succeeded` | 已完成且成功 |
| `failed` | 已完成但失败 |
| `cancelled` | 被中断、放弃或无需继续 |

### 与消息历史的关系

- **消息历史**：用于表达聊天室中“说了什么”
- **活动记录**：用于表达 Agent 运行时“做了什么”

两者面向的目标不同，因此必须解耦保存、解耦展示。

---

## 效果演示

### 场景示例：Agent 推理 + 工具调用 + compact

聊天室中，`researcher@engineering` 正在处理一条复杂请求。

前端实时显示：

```text
researcher@engineering
- [#1022][已完成] 推理：分析用户问题并规划下一步（共 96 tokens）
- [#1023][已完成] 调用工具：WebSearch "Python 3.14 new features"
- [#1024][已完成] 压缩上下文：compact 历史消息 18 条
- [#1025][进行中] 推理：整理最终答复（已接收 128 tokens）  ← 当前最新记录，`id` 最大
```

与此同时，聊天室中只出现最终自然语言消息：

```text
researcher: 我整理了一下 Python 3.14 的主要变化，重点包括 ...
```

用户无需阅读底层日志，就能直观看到 Agent 的工作过程。

### WebSocket 广播示例

```json
{
  "event": "agent_activity",
  "data": {
    "id": 1024,
    "team_id": 1,
    "agent_id": 5,
    "activity_type": "compact",
    "status": "started",
    "title": "压缩上下文",
    "detail": "compact 最近 18 条历史消息",
    "started_at": "2026-04-07T20:15:32+08:00",
    "metadata": {
      "room_id": 12,
      "model": "gpt-5.4",
      "estimated_prompt_tokens": 28750
    }
  }
}
```

推理活动在流式返回阶段可持续广播更新：

```json
{
  "event": "agent_activity",
  "data": {
    "id": 1025,
    "team_id": 1,
    "agent_id": 5,
    "activity_type": "llm_infer",
    "status": "started",
    "title": "推理",
    "detail": "分析用户问题并规划下一步",
    "started_at": "2026-04-07T20:15:33+08:00",
    "metadata": {
      "room_id": 12,
      "model": "gpt-5.4",
      "estimated_prompt_tokens": 1820,
      "current_completion_tokens": 128,
      "current_total_tokens": 1948
    }
  }
}
```

### 失败定位示例

当工具调用失败时，前端可直接展示：

```text
analyst@finance
- [失败] 调用工具：fetch_budget_sheet
  原因：file not found: /data/budget/2026-q2.xlsx
```

开发者或操作者不必再从最终聊天消息或通用日志中猜测失败点。

---

## 产品边界

### V11 包含

- 定义独立的 Agent 活动记录对象
- 后端在关键运行步骤中创建和更新活动记录
- 活动记录的持久化与查询能力
- 活动记录的 WebSocket 实时广播
- 前端可消费的结构化活动摘要字段
- 通过活动记录展示 Agent 当前运行状态与失败原因

### V11 不包含

- 完整的链路追踪平台或分布式 tracing 系统
- 面向外部 BI 的复杂统计报表
- 活动记录的长期归档与冷热分层存储
- 前端复杂可视化编排（如甘特图、火焰图）
- 将活动记录反向注入到 Agent Prompt

V11 Step 1 的目标是先把**运行态可见**做扎实，而不是一次性做成完整 observability 平台。

---

## 验收标准

- [ ] 后端新增独立于聊天消息和 history 的 `AgentActivityRecord` 数据模型。
- [ ] Agent 在至少以下阶段会产生活动记录：LLM 推理、工具调用、compact。
- [ ] 当发生 context overflow 重试时，活动记录表现为失败的 `llm_infer`、后续 `compact` 和重试 `llm_infer`，而不是单独的 `overflow_retry` 类型。
- [ ] Agent 的调度等待不单独建模为 `scheduler_wait`；后端继续维护 `active / idle` 当前运行态，并在切换时生成 `activate / idle` 活动记录。
- [ ] 每条活动记录支持 `started / succeeded / failed / cancelled` 状态流转。
- [ ] `llm_infer` 活动在流式返回期间能够持续更新当前已收到的 token 数，而不是只在结束后写入一次。
- [ ] 活动记录可持久化保存，并支持按 `agent_id`、`team_id` 或时间范围查询；房间归属信息可通过 `metadata.room_id` 过滤。
- [ ] WebSocket 可实时广播活动记录变化，前端无需轮询即可接收。
- [ ] 前端收到广播后，能够展示“正在推理”“正在调用工具”“正在压缩上下文”等运行态提示。
- [ ] 前端在推理进行中能够看到持续更新的 token 数，在推理结束后能够看到最终 token 统计。
- [ ] 活动记录失败时，能保留明确错误信息，而不是只有模糊的失败状态。
- [ ] 活动记录不会进入聊天消息历史，不影响 Agent 后续推理上下文。
- [ ] 活动记录可与一次 turn 内的多个步骤串联，便于定位问题发生在哪个环节。

---

## 使用说明

### 后端行为

当 Agent 进入关键步骤时，后端自动执行：

1. 创建一条活动记录，状态为 `started`
2. 在步骤完成后更新为 `succeeded` / `failed` / `cancelled`
3. 写入持久化存储
4. 通过 WebSocket 广播给订阅端

当步骤类型为 `llm_infer` 且模型以流式方式返回时，后端还会：

1. 在流式 chunk 到达时更新当前 token 统计
2. 将更新后的活动记录继续广播给订阅端
3. 在推理结束后写入最终 token 统计

当 Agent 被调度唤醒或重新进入等待期时，后端还会同时更新运行态，并追加对应活动记录：

1. 进入执行期时切换为 `active`
2. 进入执行期时追加一条 `activate` 活动记录
3. 完成当前处理并重新等待时切换为 `idle`
4. 回到等待期时追加一条 `idle` 活动记录

### 查询方式

活动记录建议通过独立接口查询，例如：

| 接口 | 说明 |
|------|------|
| `GET /agents/{agent_id}/activities.json` | 查询某个 Agent 的活动记录 |
| `GET /teams/{team_id}/activities.json` | 查询某个 Team 的活动记录 |
| `GET /activities.json?room_id={room_id}` | 按 `metadata.room_id` 过滤与某个房间相关的活动记录 |

### 前端使用方式

- TUI 可在 Agent 列表或房间侧边栏展示当前活动状态
- Web 前端可在聊天区域上方、Agent 卡片或调试面板中展示活动时间线
- 前端可根据 `active / idle` 当前运行态展示“激活中 / 休眠中”标记
- 前端也可基于 `activate / idle` 活动记录在时间线中还原 Agent 是何时被唤醒、何时重新休眠的
- 推理进行中可显示“已接收 tokens”，结束后切换为最终 token 统计
- 若某条活动失败，可直接展示错误摘要并跳转到相关 Agent / Room 视图

### 观察方式

- 聊天消息继续用于观察“Agent 说了什么”
- 活动记录用于观察“Agent 正在做什么”
- 两者结合后，操作者可以同时掌握结果与过程
