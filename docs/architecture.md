# 工程架构文档

> 版本：0.1.0  
> 更新时间：2025-07

---

## 1. 项目概览

本项目是一个基于大语言模型（LLM）的**多 Agent 协作平台**，支持多个 AI Agent 在聊天室中自主协作完成任务。系统提供 Web 前端、RESTful API 后端与 TUI 三种交互方式。

### 目录结构（顶层）

```
agent_team/
├── src/           # 后端 Python 源码
├── frontend/      # 前端 Vue3 源码
├── tui/           # 终端 UI（Textual）
├── tests/         # 测试代码
├── data/          # 运行时数据（SQLite DB）
├── run/           # PID 文件等运行时文件
├── docs/          # 文档
└── config/        # 配置文件（role_templates / teams 等）
```

---

## 2. 技术栈

| 层次 | 技术 |
|---|---|
| 后端框架 | Python + Tornado（asyncio 驱动） |
| LLM 调用 | LiteLLM 统一封装（支持 OpenAI / Anthropic / Google / DeepSeek） |
| ORM / 数据库 | Peewee + SQLite（`data/data.db`） |
| 前端 | Vue3 + TypeScript + Vite |
| TUI | Textual |
| 配置校验 | Pydantic v2（`AppConfig / SettingConfig / LlmServiceConfig` 等） |

---

## 3. 后端源码结构（`src/`）

```
src/
├── backend_main.py         # 启动入口（4 阶段启动流程）
├── app_entry.py            # 命令行入口
├── route.py                # Tornado 路由注册
├── constants.py            # 全局枚举定义
├── db.py                   # 数据库连接管理
├── exception.py            # 自定义异常
├── version.py              # 版本号
│
├── controller/             # HTTP 请求处理层（Tornado Handler）
│   ├── agentController.py
│   ├── roomController.py
│   ├── teamController.py
│   ├── deptController.py
│   ├── roleTemplateController.py
│   ├── configController.py
│   ├── activityController.py
│   └── wsController.py     # WebSocket 事件推送
│
├── service/                # 业务逻辑层
│   ├── messageBus.py           # 发布/订阅事件总线
│   ├── llmService.py           # LLM 服务管理（LiteLLM 封装）
│   ├── ormService.py           # ORM 初始化与数据库管理
│   ├── persistenceService.py   # 持久化状态恢复
│   ├── schedulerService.py     # 调度器（驱动房间轮次推进）
│   ├── roomService.py          # 聊天室管理（ChatRoom / 消息路由）
│   ├── teamService.py          # 团队配置管理
│   ├── deptService.py          # 部门树管理
│   ├── presetService.py        # 预设配置导入
│   ├── roleTemplateService.py  # 角色模板管理
│   ├── configService.py        # 系统配置管理
│   ├── agentActivityService.py # Agent 活动记录
│   │
│   ├── agentService/           # Agent 核心模块
│   │   ├── core.py                 # Agent 生命周期管理
│   │   ├── agent.py                # Agent 运行时实例
│   │   ├── agentTurnRunner.py      # Turn 内部逻辑（推理 + 工具调用编排）
│   │   ├── agentTaskConsumer.py    # 任务消费者
│   │   ├── agentHistoryStore.py    # 对话历史管理
│   │   ├── compact.py              # Token 预算 & 上下文压缩
│   │   ├── promptBuilder.py        # Prompt 构建
│   │   ├── toolRegistry.py         # 工具注册与执行
│   │   └── driver/                 # LLM 驱动层（策略模式）
│   │       ├── base.py                 # 驱动接口协议（AgentDriverHost）
│   │       ├── factory.py              # 驱动工厂
│   │       ├── nativeDriver.py         # 原生 OpenAI API 驱动
│   │       ├── tspDriver.py            # TSP 协议驱动
│   │       └── claudeSdkDriver.py      # Claude Agent SDK 驱动
│   │
│   └── funcToolService/        # 工具注册与执行服务
│
├── model/
│   ├── dbModel/                # Peewee ORM 模型
│   │   ├── gtAgent.py              # Agent 表
│   │   ├── gtRoom.py               # 聊天室表
│   │   ├── gtTeam.py               # 团队表
│   │   ├── gtDept.py               # 部门表
│   │   ├── gtRoleTemplate.py       # 角色模板表
│   │   ├── gtRoomMessage.py        # 消息表
│   │   ├── gtAgentHistory.py       # Agent 对话历史表
│   │   ├── gtAgentTask.py          # Agent 任务表
│   │   ├── gtAgentActivity.py      # Agent 活动记录表
│   │   ├── gtSystemConfig.py       # 系统配置表
│   │   └── historyUsage.py         # Token 用量记录
│   │
│   └── coreModel/              # 运行时数据模型（非持久化）
│       ├── gtCoreChatModel.py      # 聊天消息 / 对话上下文
│       ├── gtCoreWebModel.py       # Web 层数据模型
│       └── gtCoreAgentEvent.py     # Agent 事件模型
│
├── dal/db/                     # 数据访问层（各 Manager）
│
└── util/                       # 工具函数
    ├── configTypes.py          # Pydantic 配置模型
    ├── configUtil.py           # 配置加载
    ├── llmApiUtil.py           # LLM API 工具函数
    ├── asyncUtil.py            # 异步工具
    ├── logUtil.py              # 日志初始化
    └── assertUtil.py           # 断言工具
```

---

## 4. 启动流程（`backend_main.py`，4 阶段）

```
阶段 1 / 基础 Service 启动
  messageBus → llmService → funcToolService → ormService
  → persistenceService → agentService → roomService
  → schedulerService → presetService

阶段 2 / 导入配置
  presetService.import_from_app_config()
  （导入 RoleTemplate / Team / Dept / Room 预设）

阶段 3 / 构建运行时
  agentService.load_all_team()      # 加载所有团队成员
  roomService.load_rooms_from_db()  # 从数据库恢复聊天室

阶段 4 / 恢复持久化状态
  agentService.restore_state()
  roomService.restore_state()
  schedulerService.start_scheduling()  # 开始调度
```

启动完成后，Tornado HTTP Server 在 `0.0.0.0:{port}`（默认 8080）监听。

---

## 5. 核心模块说明

### 5.1 事件总线（`messageBus`）

采用**发布/订阅**模式，所有跨模块通信通过 `MessageBusTopic` 枚举主题解耦。

| 主题 | 触发时机 |
|---|---|
| `ROOM_MSG_ADDED` | 房间新增消息 |
| `ROOM_STATUS_CHANGED` | 房间状态/发言人变更 |
| `AGENT_STATUS_CHANGED` | Agent 忙闲状态变更 |
| `AGENT_ACTIVITY_CHANGED` | Agent 活动记录变更 |

回调统一在 asyncio 事件循环中异步调度，避免慢订阅者阻塞发布链路。

### 5.2 聊天室（`roomService.ChatRoom`）

- 维护消息历史、参与者列表、每个 Agent 的消息读取进度
- 支持两种房间类型：`PRIVATE`（1v1 Human+Agent）和 `GROUP`（多 Agent 自治群聊）
- 轮次调度：`_turn_pos` 指针轮询参与者列表，`_turn_count` 记录完整轮次数
- 房间状态：`INIT → SCHEDULING → IDLE`

### 5.3 Agent Turn 执行（`agentTurnRunner.AgentTurnRunner`）

每次 Agent 发言对应一个 Turn，执行流程：

```
1. 同步房间新消息到 Agent 历史
2. 构建 Prompt（system prompt + 对话历史）
3. 调用 LLM Driver 推理
4. 解析工具调用 → 执行工具 → 将结果追加历史
5. 重复 3-4（host loop）直至 LLM 不再调用工具或达到重试上限
6. 将最终回复发送到房间
```

### 5.4 LLM 驱动层（`agentService/driver/`）

采用**策略模式**，支持三种驱动：

| 驱动类型 | 说明 |
|---|---|
| `NATIVE` | 原生 OpenAI 兼容 API |
| `TSP` | TSP 协议驱动（Thinking-Step Protocol） |
| `CLAUDE_SDK` | Claude Agent SDK 原生驱动 |

驱动由 `factory.py` 根据 `AgentDriverConfig.driver_type` 工厂创建。

### 5.5 上下文压缩（`compact.py`）

当对话历史 Token 数接近模型上下文窗口阈值时自动触发：

- 阈值由 `LlmServiceConfig.compact_trigger_ratio`（默认 0.85）控制
- 调用 LLM 对历史进行摘要压缩，保留关键信息
- 支持多种模型的上下文窗口长度内置默认值（可通过配置覆盖）

### 5.6 配置系统（`util/configTypes.py`）

使用 Pydantic v2 进行配置校验，主要配置模型层次：

```
AppConfig
├── setting: SettingConfig
│   ├── llm_services: List[LlmServiceConfig]   # LLM 服务列表
│   ├── persistence: PersistenceConfig          # 持久化配置
│   └── workspace_root: str                     # 工作区根目录
├── role_templates: List[RoleTemplateConfig]    # 角色模板
└── teams: List[TeamConfig]                     # 团队配置
    ├── agents: List[AgentConfig]
    ├── dept_tree: DeptNodeConfig               # 部门树（递归）
    └── preset_rooms: List[TeamRoomConfig]
```

---

## 6. API 路由（`route.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/config/frontend.json` | 前端配置 |
| GET | `/role_templates/list.json` | 角色模板列表 |
| POST | `/role_templates/create.json` | 创建角色模板 |
| GET/PUT/DELETE | `/role_templates/{name}.json` | 角色模板详情/修改/删除 |
| GET | `/agents/list.json` | Agent 列表 |
| GET | `/agents/{id}.json` | Agent 详情 |
| POST | `/agents/{id}/resume.json` | 恢复 Agent |
| GET | `/teams/{id}/agents/{name}.json` | 团队 Agent 详情 |
| GET/POST | `/rooms/{id}/messages/list.json` | 房间消息列表/发送 |
| WS | `/ws/events.json` | WebSocket 实时事件推送 |
| GET | `/teams/list.json` | 团队列表 |
| POST | `/teams/create.json` | 创建团队 |
| GET/PUT/DELETE | `/teams/{id}.json` | 团队详情/修改/删除 |
| GET | `/teams/{id}/rooms/list.json` | 团队房间列表 |
| GET/PUT | `/teams/{id}/dept_tree.json` | 部门树查询/更新 |
| GET | `/activities.json` | 全局活动记录 |
| GET | `/agents/{id}/activities.json` | Agent 活动记录 |

---

## 7. 前端（`frontend/`）

基于 **Vue3 + TypeScript + Vite** 构建的单页应用（SPA）。

### 页面结构

| 页面 | 文件 | 说明 |
|---|---|---|
| 控制台 | `ConsolePage.vue` | 主聊天界面 |
| 设置 | `SettingsPage.vue` | 系统设置 |
| 团队创建 | `TeamCreatePage.vue` | 创建/配置团队 |
| 团队详情 | `TeamDetailPage.vue` | 团队详情与管理 |

### 关键模块

- `api.ts`：封装所有后端 API 调用
- `teamStore.ts`：团队状态管理（Pinia/Reactive）
- `realtime/`：WebSocket 实时事件处理
- `composables/`：Vue Composable 函数
- `router.ts`：Vue Router 路由配置

---

## 8. 数据模型（`model/dbModel/`）

| 模型 | 表 | 说明 |
|---|---|---|
| `GtAgent` | gt_agent | Agent 配置与状态 |
| `GtRoom` | gt_room | 聊天室配置 |
| `GtTeam` | gt_team | 团队配置 |
| `GtDept` | gt_dept | 部门树节点 |
| `GtRoleTemplate` | gt_role_template | 角色模板 |
| `GtRoomMessage` | gt_room_message | 聊天消息记录 |
| `GtAgentHistory` | gt_agent_history | Agent 对话历史（LLM 上下文） |
| `GtAgentTask` | gt_agent_task | Agent 任务队列 |
| `GtAgentActivity` | gt_agent_activity | Agent 活动记录（推理/工具调用） |
| `GtSystemConfig` | gt_system_config | 系统配置 KV 存储 |
| `HistoryUsage` | history_usage | Token 用量记录 |

所有模型基于 Peewee ORM，数据库为 SQLite（`data/data.db`）。

---

## 9. 枚举常量（`constants.py`）

| 枚举 | 说明 |
|---|---|
| `LlmServiceType` | LLM 服务类型（openai-compatible / anthropic / google / deepseek） |
| `DriverType` | Agent 驱动类型（native / tsp / claude_sdk） |
| `RoomType` | 房间类型（PRIVATE / GROUP） |
| `RoomState` | 房间状态（INIT / SCHEDULING / IDLE） |
| `AgentStatus` | Agent 状态（ACTIVE / IDLE / FAILED） |
| `EmployStatus` | 在职状态（ON_BOARD / OFF_BOARD） |
| `MessageBusTopic` | 事件总线主题 |
| `AgentActivityType` | 活动类型（LLM_INFER / TOOL_CALL / COMPACT / AGENT_STATE） |
| `AgentHistoryTag` | 历史记录标签（ROOM_TURN_BEGIN / COMPACT_SUMMARY 等） |
| `TurnStepResult` | Turn 步骤执行结果 |

---

## 10. 关键数据流

```
Operator / 前端
    │  HTTP POST /rooms/{id}/messages/send.json
    ▼
roomController → roomService.send_message()
    │  messageBus.publish(ROOM_MSG_ADDED)
    ▼
schedulerService（监听 ROOM_STATUS_CHANGED）
    │  调度下一个发言 Agent
    ▼
agentTaskConsumer → AgentTurnRunner.run()
    │  1. 构建 Prompt
    │  2. 调用 LLM Driver（nativeDriver / tspDriver / claudeSdkDriver）
    │  3. 解析工具调用 → funcToolService 执行
    │  4. 追加历史 → 继续 host loop
    │  5. 发送回复到房间
    ▼
roomService.send_message()（Agent 回复）
    │  messageBus.publish(ROOM_MSG_ADDED)
    ▼
wsController → WebSocket 推送给前端
```
