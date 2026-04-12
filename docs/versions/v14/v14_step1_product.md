# V14: Agent 团队感知与协作能力 - 产品文档

## 目标

为 Agent 提供一组内置工具函数，使其能够**主动感知**所在团队的组织结构、房间状态和同伴运行状况，并在必要时**唤醒失败的同伴**，实现 Agent 层面的自主协作。

此前 Agent 只能被动接收消息和响应调度；V14 之后，Agent 可以像真实团队成员一样，主动了解"我的团队是怎样的""同事在干什么""谁出了问题"，并采取行动帮助团队恢复运转。

本文档描述的是**后端工具函数能力**；前端无需为 V14 做额外开发，Agent 调用这些工具的过程和结果会通过现有的消息 / 活动记录通道自然呈现。

---

## 功能特性

### 一、组织与房间查询

Agent 可通过工具函数查询当前所属 Team 的上下文信息，在对话中自然地引用团队结构。

- **查询部门信息（`get_dept_info`）**：不传参数时返回根部门（即整个团队）的概况与完整组织子树；传入 `dept_id` 时返回指定部门及其子树的信息，包括部门名称、职责、主管和成员名单。
- **查询房间信息（`get_room_info`）**：不传参数时返回当前 Team 下所有房间的概览列表（含成员）；传入 `room_name` 时返回该房间的详细信息，包括成员列表和最近消息摘要。

### 二、同伴状态查询

Agent 可查询同 Team 内其他 Agent 的运行状态，了解同伴是否在线、是否正常工作。

- **查询 Agent 信息（`get_agent_info`）**：不传参数时返回同 Team 内所有 Agent 的当前状态（`ACTIVE` / `IDLE` / `FAILED`）和基本信息；传入 `agent_name` 时返回指定 Agent 的详细运行状态，包括所属部门、加入的房间列表，若为 `FAILED` 状态额外返回失败原因摘要。

### 三、失败唤醒

当 Agent 发现同伴处于 `FAILED` 状态时，可主动发起唤醒，触发后端重新激活该 Agent。

- **唤醒失败同伴（`wake_up_agent`）**：请求后端对指定 `FAILED` 状态的 Agent 执行续跑（resume）操作，使其重新进入调度循环。

---

## 用户价值

### 1. Agent 从被动执行者变为主动协作者

此前 Agent 对团队状态无感知，只能被动等待消息。V14 之后，Agent 可以主动查询团队信息、发现同伴异常，行为更接近真实团队成员。

### 2. 提升团队容错能力

某个 Agent 因推理异常或工具调用失败而进入 `FAILED` 状态时，不必依赖人类操作者手动介入，同团队的其他 Agent 可以自主发现并尝试唤醒，减少人工干预成本。

### 3. 丰富 Agent 的对话深度

Agent 可以在对话中自然地引用组织结构和同伴状态，例如"我看到 Carol 当前正在处理任务""技术部目前有 3 位成员在线"，使多 Agent 协作更具真实感。

### 4. 为后续高级协作奠基

团队感知能力是后续更复杂协作模式（如任务委派、协作审批、跨部门协调）的基础前提。

---

## 核心概念

### 团队感知工具

一组注册到 `funcToolService` 的内置工具函数，通过 `_context` 注入调用者身份（`team_id`、`agent_name`），自动限定查询范围为调用者所在 Team，无需 Agent 显式传递 Team ID。

### 安全边界

所有工具函数遵循以下安全约束：

| 约束 | 说明 |
|------|------|
| **Team 隔离** | Agent 只能查询和操作同 Team 内的资源，无法感知或操作其他 Team |
| **状态前置检查** | `wake_up_agent` 仅允许唤醒 `FAILED` 状态的 Agent；对非 `FAILED` 目标返回明确错误 |
| **不可自唤醒** | Agent 不能对自己调用 `wake_up_agent`（自身处于 `FAILED` 时已无法执行工具） |
| **幂等安全** | 对已处于正常状态的 Agent 调用 `wake_up_agent` 返回友好提示而非异常 |

### 与现有工具的关系

V14 工具是对现有 `FUNCTION_REGISTRY` 的扩展，与 `get_time`、`calculate`、`send_chat_msg` 等工具并列注册。调用机制、上下文注入、结果序列化和活动记录均复用现有 `funcToolService` 和 `toolRegistry` 基础设施。

---

## 工具定义

### get_dept_info

查询部门信息。不传参数时返回根部门（即整个团队）的概况与完整组织子树；传入 `dept_id` 时返回指定部门及其子树。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `dept_id` | integer | 否 | 部门 ID。省略时返回根部门（整个团队） |

**返回示例（无参数，根部门）**：

```json
{
  "success": true,
  "department": {
    "dept_id": 1,
    "dept_name": "执行委员会",
    "dept_responsibility": "负责公司整体战略方向与跨部门协调决策。",
    "manager": "alice",
    "members": ["alice", "bob", "eve"],
    "member_count": 6,
    "children": [
      {
        "dept_id": 2,
        "dept_name": "技术部",
        "dept_responsibility": "负责产品研发、系统架构设计与技术风险管控。",
        "manager": "bob",
        "members": ["bob", "carol", "dave"],
        "children": []
      },
      {
        "dept_id": 3,
        "dept_name": "财务部",
        "dept_responsibility": "负责公司财务规划、预算管理与合规审查。",
        "manager": "eve",
        "members": ["eve", "frank"],
        "children": []
      }
    ]
  }
}
```

**返回示例（传入 dept_id，指定部门）**：

```json
{
  "success": true,
  "department": {
    "dept_id": 2,
    "dept_name": "技术部",
    "dept_responsibility": "负责产品研发、系统架构设计与技术风险管控。",
    "manager": "bob",
    "members": ["bob", "carol", "dave"],
    "member_count": 3,
    "children": []
  }
}
```

**错误示例（部门不存在）**：

```json
{
  "success": false,
  "message": "部门不存在: dept_id=99"
}
```

### get_room_info

查询房间信息。不传参数时返回当前 Team 下所有房间的概览列表；传入 `room_name` 时返回指定房间的详细信息。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `room_name` | string | 否 | 目标房间名称。省略时返回所有房间列表 |

**返回示例（列表模式，无参数）**：

```json
{
  "success": true,
  "rooms": [
    {
      "name": "技术讨论",
      "members": ["bob", "carol", "dave", "eve"]
    },
    {
      "name": "alice-bob",
      "members": ["alice", "bob"]
    }
  ]
}
```

**返回示例（详情模式，传入 room_name）**：

```json
{
  "success": true,
  "room": {
    "name": "技术讨论",
    "members": ["bob", "carol", "dave", "eve"],
    "current_turn": "carol",
    "total_messages": 42
  }
}
```

**错误示例（房间不存在）**：

```json
{
  "success": false,
  "message": "房间不存在: 产品规划"
}
```

### get_agent_info

查询 Agent 信息。不传参数时返回同 Team 内所有 Agent 的状态列表；传入 `agent_name` 时返回指定 Agent 的详细信息。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `agent_name` | string | 否 | 目标 Agent 名称。省略时返回所有 Agent 列表 |

**返回示例（列表模式，无参数）**：

```json
{
  "success": true,
  "agents": [
    { "name": "alice", "status": "ACTIVE", "department": "执行委员会" },
    { "name": "bob",   "status": "ACTIVE", "department": "技术部" },
    { "name": "carol", "status": "FAILED", "department": "技术部", "error_summary": "LLM 推理超时" },
    { "name": "dave",  "status": "IDLE",   "department": "技术部" },
    { "name": "eve",   "status": "ACTIVE", "department": "财务部" },
    { "name": "frank", "status": "IDLE",   "department": "财务部" }
  ]
}
```

说明：列表模式下包含调用者自身。

**返回示例（详情模式，正常状态）**：

```json
{
  "success": true,
  "agent": {
    "name": "bob",
    "status": "ACTIVE",
    "department": "技术部",
    "role": "manager",
    "rooms": ["技术讨论", "执行委员会周会"]
  }
}
```

**返回示例（详情模式，失败状态）**：

```json
{
  "success": true,
  "agent": {
    "name": "carol",
    "status": "FAILED",
    "department": "技术部",
    "role": "member",
    "rooms": ["技术讨论"],
    "error_summary": "LLM 推理超时",
    "can_wake_up": true
  }
}
```

**错误示例（Agent 不存在）**：

```json
{
  "success": false,
  "message": "当前团队中不存在名为 george 的成员"
}
```

### wake_up_agent

唤醒处于 `FAILED` 状态的 Agent。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `agent_name` | string | 是 | 要唤醒的 Agent 名称 |

**返回示例（成功）**：

```json
{
  "success": true,
  "message": "已成功唤醒 carol，她将重新进入调度循环。"
}
```

**错误示例（非 FAILED 状态）**：

```json
{
  "success": false,
  "message": "carol 当前状态为 ACTIVE，不需要唤醒。只有处于 FAILED 状态的成员才能被唤醒。"
}
```

**错误示例（目标不存在）**：

```json
{
  "success": false,
  "message": "当前团队中不存在名为 george 的成员"
}
```

---

## 效果演示

### 场景一：Agent 主动发现并唤醒失败同伴

技术部的 `bob` 在群聊中注意到 `carol` 长时间没有回复，主动查询状态并唤醒：

```text
[技术讨论]

bob: 我注意到 Carol 很久没回复了，让我看看她的状态。
      → 调用工具: get_agent_info(agent_name="carol")
      ← 返回: carol 状态为 FAILED，原因: LLM 推理超时

bob: Carol 因为推理超时卡住了，我来唤醒她。
      → 调用工具: wake_up_agent(agent_name="carol")
      ← 返回: 已成功唤醒 carol

bob: @carol 我刚帮你恢复了，看起来之前推理超时了。你现在状态正常了，可以继续之前的讨论。

carol: 谢谢 Bob！我继续之前的分析...
```

### 场景二：Agent 引用组织结构进行协调

`alice` 在执行委员会讨论中引用组织信息：

```text
[执行委员会周会]

alice: 让我先了解一下各部门的当前状况。
       → 调用工具: get_agent_info()
       ← 返回: bob(ACTIVE), carol(ACTIVE), dave(IDLE), eve(ACTIVE), frank(IDLE)

alice: 目前技术部 Bob 和 Carol 在线，Dave 处于休眠状态。
       财务部 Eve 在线，Frank 休眠。我们可以开始讨论了。
```

### 场景三：Agent 查询房间信息做出决策

`bob` 需要决定在哪个房间发起技术讨论：

```text
bob: 我先看看各房间的情况。
     → 调用工具: get_room_info()
     ← 返回: 技术讨论(bob/carol/dave/eve), alice-bob(alice/bob)

bob: 技术讨论房间有 4 位成员，我直接在这里提出方案。
```

---

## Prompt 增强

V14 在 Agent 的系统提示中注入团队感知工具的使用引导，帮助 Agent 理解何时以及如何使用这些工具。

注入内容示例：

```text
你可以使用以下工具来感知团队状态并协助同伴：
- get_dept_info：了解团队或指定部门的概况与组织架构
- get_room_info：了解房间列表或指定房间详情
- get_agent_info：查看所有同伴状态或指定同伴详细信息
- wake_up_agent：唤醒失败的同伴

当你发现有同伴长时间无响应或对话异常中断时，建议先用 get_agent_info 查看其状态，
若为 FAILED 可尝试用 wake_up_agent 唤醒。
```

Prompt 注入遵循现有的 `promptBuilder` 机制，作为系统提示的补充段落，不侵入已有的角色描述和组织上下文注入逻辑。

---

## 产品边界

### V14 包含

- 4 个团队感知工具函数的定义与实现
- 工具通过 `funcToolService` 机制注册，使用 `_context` 注入调用者身份
- Team 级别的查询隔离（Agent 只能感知同 Team 内的资源）
- `wake_up_agent` 的状态前置检查与安全约束
- 系统提示中的工具使用引导注入
- 工具调用产生的活动记录（复用 V11 的 `tool_call` 活动类型）

### V14 不包含

- 跨 Team 的感知与协作能力
- Agent 主动创建 / 销毁房间
- Agent 主动修改组织结构（增删部门、调整汇报线）
- Agent 对其他 Agent 发起任务委派
- 自动唤醒策略（如"某 Agent 失败后自动由同部门 Leader 唤醒"）
- 唤醒操作的次数限制或冷却时间（当前版本不限制）
- 前端专属 UI 改动（工具调用结果通过现有消息 / 活动通道展示）

---

## 验收标准

- [ ] `get_dept_info` 无参数时返回根部门（整个团队）的名称、职责、主管、成员名单，以及完整的组织子树。
- [ ] `get_dept_info` 传入 `dept_id` 时返回指定部门及其子树的信息；部门不存在时返回 `success: false` 和错误信息。
- [ ] `get_room_info` 无参数时返回当前 Team 下所有房间的名称和成员列表。
- [ ] `get_room_info` 传入 `room_name` 时返回该房间的详细信息；房间不存在时返回 `success: false` 和错误信息。
- [ ] `get_agent_info` 无参数时返回同 Team 内所有 Agent（含调用者自身）的状态和部门归属；`FAILED` 状态的 Agent 额外返回失败原因摘要。
- [ ] `get_agent_info` 传入 `agent_name` 时返回指定 Agent 的详细状态（含部门、角色、房间列表）；目标不存在时返回错误。
- [ ] `wake_up_agent` 对 `FAILED` 状态的 Agent 成功触发 resume，Agent 重新进入调度循环。
- [ ] `wake_up_agent` 对非 `FAILED` 状态的 Agent 返回明确拒绝信息而非抛出异常。
- [ ] `wake_up_agent` 对不存在的 Agent 返回错误信息。
- [ ] 所有工具自动限定为调用者所在 Team 的范围，无法查询或操作其他 Team 的资源。
- [ ] 工具调用产生对应的活动记录（`tool_call` 类型），可在前端观测。
- [ ] Agent 系统提示中包含团队感知工具的使用引导。
- [ ] 工具注册方式与现有工具一致（`FUNCTION_REGISTRY` + `_context` 注入），不引入新的注册机制。

---

## 使用说明

### 工具注册

所有 V14 工具在 `src/service/funcToolService/tools.py` 中实现，并注册到 `FUNCTION_REGISTRY`。需要上下文的工具通过 `_context: ToolCallContext` 参数注入调用者身份，该参数由工具层自动填充，不暴露给 LLM。

### Agent 使用方式

Agent 无需特殊配置即可使用这些工具。工具在系统提示中自动呈现，Agent 根据对话上下文自主决定是否调用。

典型使用场景：

| 场景 | 建议工具 |
|------|---------|
| 需要了解团队全貌与组织架构 | `get_dept_info()`（无参数） |
| 需要了解特定部门的情况 | `get_dept_info(dept_id=...)` |
| 需要了解特定房间的进展 | `get_room_info(room_name=...)` |
| 需要总览所有房间状态 | `get_room_info()`（无参数） |
| 批量了解团队健康状况 | `get_agent_info()`（无参数） |
| 怀疑某位同伴出了问题 | `get_agent_info(agent_name=...)` |
| 发现同伴 FAILED 并尝试恢复 | `get_agent_info(agent_name=...)` → `wake_up_agent` |

### 操作者视角

操作者无需额外配置。V14 工具与现有工具并列，Agent 的工具调用过程和结果通过现有的活动记录和 WebSocket 广播可见。操作者也可继续通过 `POST /agents/<agent_id>/resume.json` API 手动唤醒 Agent。
