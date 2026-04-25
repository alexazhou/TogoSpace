# Agent 体验改进：平台改造建议文档

> 来源：Agent 运行过程中的真实痛点反馈  
> 整理人：小马哥（项目经理 Agent）  
> 日期：2025-07

---

## 背景

本文档来源于 Agent 在实际运行过程中积累的真实体验反馈，涵盖工具能力缺失、上下文管理不足、可观测性不完善等问题。  
部分改进需要在 **agent_team 平台**（后端 + 前端）侧实现，部分需要在 **GTAgentHands 工具层**实现，本文重点描述 agent_team 平台侧的改造需求。

---

## 一、对话窗口内联活动展示

### 问题描述
Agent 调用工具时，执行过程和结果仅记录在活动表（`GtAgentActivity`）中，前端需要主动打开单独的活动弹窗才能看到。对话窗口内无任何工具执行的可见反馈，Operator 难以判断 Agent 是"在干活"还是"卡住了"。

### 期望效果
在聊天室对话窗口中，以折叠卡片的形式内联展示工具调用过程，类似 Claude/Cursor 的交互方式：

```
┌─────────────────────────────────────────┐
│ 🔧 execute_bash  ✅ 已完成  耗时 1.2s   │  ← 折叠态（默认）
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ 🔧 execute_bash  ✅ 已完成  耗时 1.2s ▼ │  ← 展开态
│ $ ls /src/service/                      │
│ agentService/  llmService.py  ...       │
└─────────────────────────────────────────┘
```

- 工具执行中：显示 spinner + 工具名
- 执行完成：显示耗时 + 成功/失败状态
- 点击可展开查看输入参数和输出结果
- LLM 推理中：显示"正在思考..."提示

### 平台侧改造点
- 后端：`AgentActivity` 事件已通过 WebSocket 广播（V11 已实现），无需额外改动
- 前端：在 `ConsolePage.vue` 的消息列表中，将 `AGENT_ACTIVITY_CHANGED` 事件渲染为内联卡片组件
- 前端：新增 `ActivityCard.vue` 组件，支持折叠/展开

---

## 二、Agent 持久化便签（agent_notepad）

### 问题描述
Agent 的状态完全依赖对话历史传递。当历史过长触发 compact 压缩时，任务进度、已收集的中间结果等关键信息可能丢失。平台重启后，长任务进度无法恢复。

### 期望效果
为每个 Agent 提供一块独立的持久化键值存储（"便签"），特性如下：
- 数据独立存储，不进入对话历史，**不受 compact 压缩影响**
- 平台重启后数据仍然存在
- Agent 通过 `agent_notepad` 工具读写，支持 `write / read / delete / list / clear` 操作
- 每个 Agent 的便签空间互相隔离

### 平台侧改造点

#### 数据库层
新增 `GtAgentNotepad` 表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `agent_id` | INTEGER | 关联 Agent |
| `team_id` | INTEGER | 关联 Team |
| `key` | VARCHAR | 便签键名 |
| `value` | TEXT | 便签内容 |
| `updated_at` | DATETIME | 最后更新时间 |

唯一索引：`(agent_id, key)`

#### Service 层
新增 `agentNotepadService.py`，提供：
- `read(agent_id, key) -> str | None`
- `write(agent_id, key, value) -> None`
- `delete(agent_id, key) -> None`
- `list_keys(agent_id) -> List[str]`
- `clear(agent_id) -> int`

#### 工具层（GTAgentHands）
新增 `agent_notepad` 工具，调用 agent_team 后端 API 完成读写。  
详细工具规格见：`GTAgentHands/doc/tool_spec/agent_notepad.md`

#### API 层
新增 REST 接口：
- `GET /agents/{id}/notepad.json` — 列出所有 key
- `GET /agents/{id}/notepad/{key}.json` — 读取指定 key
- `POST /agents/{id}/notepad/{key}/write.json` — 写入
- `POST /agents/{id}/notepad/{key}/delete.json` — 删除
- `POST /agents/{id}/notepad/clear.json` — 清空

---

## 三、任务委派工具（task_delegate）

### 问题描述
Agent 之间协作目前只能通过聊天室发消息安排任务，缺乏结构化的任务生命周期管理：
- 无法确认对方是否接受了任务
- 任务结果只能靠对方在聊天室回复，容易淹没在消息流中
- 委派方无法主动查询任务状态

### 期望效果
提供结构化的任务委派机制，支持完整的任务生命周期：
```
pending → accepted → in_progress → completed
                                 → failed
                                 → timeout
```

### 平台侧改造点

#### 数据库层
新增 `GtAgentTask`（已存在，确认是否可复用或扩展）表，补充以下字段（如不足）：
- `delegator_id`：委派方 Agent ID
- `assignee_id`：被委派方 Agent ID
- `title`：任务标题
- `description`：任务详细描述
- `status`：任务状态枚举
- `result`：任务完成结果
- `fail_reason`：失败原因
- `deadline_at`：超时时间
- `created_at / updated_at`

#### Service 层
新增 `agentTaskDelegateService.py`，提供任务的创建、查询、完成、失败操作，并在状态变更时通过 `messageBus` 广播事件。

#### 工具层（GTAgentHands）
新增 `task_delegate` 工具。  
详细工具规格见：`GTAgentHands/doc/tool_spec/task_delegate.md`

#### API 层
- `POST /agents/{id}/tasks/create.json`
- `GET /agents/{id}/tasks/list.json`
- `GET /tasks/{task_id}.json`
- `POST /tasks/{task_id}/complete.json`
- `POST /tasks/{task_id}/fail.json`

---

## 四、延迟提醒工具（remind_me）

### 问题描述
Agent 在等待异步任务（如等待其他 Agent 完成、等待某个时间节点）时，只能依赖外部消息触发，无法主动设定"N 分钟后再来检查"的逻辑，导致要么频繁轮询，要么完全依赖人工触发。

### 期望效果
Agent 可注册延迟提醒，由调度器在指定时间后向 Agent 所在房间注入一条系统消息，触发 Agent 的下一轮行动。

### 平台侧改造点

#### 数据库层
新增 `GtAgentReminder` 表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `agent_id` | INTEGER | 注册提醒的 Agent |
| `room_id` | INTEGER | 触发提醒时注入消息的房间 |
| `message` | TEXT | 提醒内容 |
| `trigger_at` | DATETIME | 触发时间 |
| `status` | VARCHAR | `pending / triggered / cancelled` |

#### Service 层
新增 `agentReminderService.py`：
- `set_reminder(agent_id, room_id, message, delay_minutes) -> reminder_id`
- `cancel_reminder(reminder_id) -> None`
- `list_reminders(agent_id) -> List`

`schedulerService` 在每个调度周期检查到期的提醒，调用 `roomService.send_system_message()` 注入提醒内容。

#### 工具层（GTAgentHands）
新增 `remind_me` 工具。  
详细工具规格见：`GTAgentHands/doc/tool_spec/remind_me.md`

---

## 五、工具使用规范补充到系统 Prompt

### 问题描述
Agent 在使用 `execute_bash` 时，会自发地尝试用 heredoc 写文件，而这种方式在 `bash -c` 环境下会因 LLM 生成单行字面量命令而静默失败（详见 `workspace_root/默认团队/heredoc_test/report.md`）。根本原因是 LLM 生成命令时的结构性错误，属于已知的 LLM code generation 边界问题。

### 期望效果
在 Agent 系统 Prompt 中明确加入工具使用规范，从源头约束 LLM 的生成行为：

```
工具使用规范：
- 写入文件：优先使用 write_file 工具；如需用 bash，使用 python3 脚本方式，
  禁止在 execute_bash 中使用 heredoc（bash -c 环境下 heredoc 会静默失败）
- write_file 工具只能写入 workspace_root 以内的路径；
  跨目录写入请使用 execute_bash + python3
```

### 平台侧改造点
- 在 `AppConfig` 的 `group_chat_prompt` 或新增的 `tool_usage_prompt` 字段中加入上述规范
- 或在 `promptBuilder.build_agent_system_prompt()` 中追加固定的工具使用规范段落
- 建议作为配置项而非硬编码，便于后续调整

---


---

## 六、gTSP 工具层改造（GTAgentHands）

gTSP 是 agent_team 平台使用的工具执行服务（Tool Server Protocol 实现），Agent 通过它调用 `execute_bash`、`read_file`、`write_file` 等工具。以下新工具需要在 gTSP 层实现并注册。

### 6.1 新增 `http_request` 工具

#### 改造动机
Agent 目前只能通过 `execute_bash` 调用 `curl` 发起 HTTP 请求，结果是非结构化文本，解析困难，错误处理不透明。需要一个原生的结构化 HTTP 工具。

#### 实现要点（Go 层）
新增 `src/tools/http_request.go`：
- 使用 Go 标准库 `net/http` 实现，支持 GET / POST / PUT / PATCH / DELETE
- 支持自定义 Headers 和 JSON/文本 Body
- 响应体自动尝试 JSON 解析，失败则返回原始文本
- 超时控制（默认 30s，可通过参数覆盖）
- 响应体超过阈值（50KB）时自动截断，设置 `truncated: true`
- 沙箱控制：复用现有 `session.IsNetworkAllowed()` 检查，网络未授权时返回错误

#### 新增参数
```go
type HttpRequestParams struct {
    URL     string            `json:"url"`
    Method  string            `json:"method,omitempty"`   // 默认 GET
    Headers map[string]string `json:"headers,omitempty"`
    Body    json.RawMessage   `json:"body,omitempty"`
    Timeout int               `json:"timeout,omitempty"`  // 秒，默认 30
}
```

#### 工具规格文档
详见：`GTAgentHands/doc/tool_spec/http_request.md`

---

### 6.2 新增 `web_search` 工具

#### 改造动机
Agent 的知识存在训练截止日期，无法获取实时信息。需要一个标准化的搜索工具，让 Agent 能查询最新资讯和技术文档。

#### 实现要点（Go 层）
新增 `src/tools/web_search.go`：
- 对接搜索引擎 API（可配置，如 Google Custom Search API、Bing Search API 或 SearXNG 自托管实例）
- API Key 和搜索引擎 ID 通过环境变量或启动参数配置，不硬编码
- 返回结构化结果列表（title / snippet / url）
- 沙箱控制：同样受 `session.IsNetworkAllowed()` 约束
- 结果数量上限可配置，默认返回 5 条

#### 新增参数
```go
type WebSearchParams struct {
    Query      string `json:"query"`
    NumResults int    `json:"num_results,omitempty"` // 默认 5，最大 20
    Lang       string `json:"lang,omitempty"`        // 如 "zh"、"en"
}
```

#### 工具规格文档
详见：`GTAgentHands/doc/tool_spec/web_search.md`

---

### 6.3 新增 `send_chat_msg` / `finish_chat_turn` 工具的沙箱感知

#### 改造动机
`send_chat_msg` 和 `finish_chat_turn` 是 Agent 与平台交互的核心工具，目前由 agent_team 后端直接注入，不经过 gTSP。但在 TSP 驱动模式（`tspDriver`）下，所有工具调用都经过 gTSP 路由，需要确保这两个平台级工具能正确穿透 gTSP 的工具过滤逻辑，不被沙箱误拦截。

#### 实现要点
- 在 gTSP 的 `initialize` 响应中，将平台注入的工具（`send_chat_msg`、`finish_chat_turn` 等）标记为 `platform_native: true`，表示由上层平台实现，gTSP 不负责执行
- 当 `tool` 请求的工具名为平台原生工具时，gTSP 直接透传请求给上层，不进行本地路由
- 或者：维持现有架构（平台工具不经过 gTSP），但在 `initialize` 的工具列表中明确区分"gTSP 工具"和"平台工具"，避免 Agent 混淆

---

### 6.4 execute_bash 工具文档修正

#### 改造动机
现有 `doc/tool_spec/execute_bash.md` 中的参数名与实际实现不一致：
- 文档写的是 `timeout`，实际参数名是 `task_timeout`
- 文档写的是 `is_background`，实际参数名是 `run_in_background`

同时缺少对 heredoc 陷阱的警告说明。

#### 需要修改
1. 更新 `doc/tool_spec/execute_bash.md` 中的参数名，与 `execute_bash.go` 中的实际定义保持一致
2. 在工具描述中补充警告：
   > ⚠️ 不要在 `command` 中使用 heredoc（`<< EOF`）。由于 LLM 生成命令时倾向于将多行结构压缩为单行，heredoc 的 EOF 终止符无法被正确识别，会导致静默失败（exit_code=0 但文件未写入）。写文件请使用 `write_file` 工具或 `python3 -c` 脚本。

---

## 七、改造优先级汇总

| 优先级 | 改造项 | 实现位置 | 复杂度 | 价值 |
|---|---|---|---|---|
| P0 | execute_bash 文档修正（参数名+heredoc警告） | gTSP | 低 | 消除文档误导，防止 heredoc 静默失败 |
| P0 | 工具使用规范补充到系统 Prompt | agent_team | 低 | 从 Prompt 层约束 LLM，立即生效 |
| P1 | 对话窗口内联活动展示（折叠卡片） | agent_team 前端 | 中 | 大幅提升 Operator 对 Agent 状态的感知 |
| P1 | Agent 持久化便签（agent_notepad） | agent_team + gTSP | 中 | 解决长任务状态丢失问题 |
| P2 | http_request 工具 | gTSP | 中 | 结构化 HTTP 调用，替代 curl bash |
| P2 | 延迟提醒工具（remind_me） | agent_team | 中 | 支持异步等待场景 |
| P2 | web_search 工具 | gTSP | 中 | 实时信息获取，突破知识截止限制 |
| P3 | 任务委派工具（task_delegate） | agent_team | 高 | 提升多 Agent 协作的结构化程度 |

---

## 八、相关文档

- 工具规格文档：`GTAgentHands/doc/tool_spec/agent_notepad.md`
- 工具规格文档：`GTAgentHands/doc/tool_spec/task_delegate.md`
- 工具规格文档：`GTAgentHands/doc/tool_spec/remind_me.md`
- 工具规格文档：`GTAgentHands/doc/tool_spec/http_request.md`
- 工具规格文档：`GTAgentHands/doc/tool_spec/web_search.md`
- heredoc 静默失败实验报告：`workspace_root/默认团队/heredoc_test/report.md`
- 平台架构文档：`docs/architecture.md`
